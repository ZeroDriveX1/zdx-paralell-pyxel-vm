package com.zerodrivex.zdxnode

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.os.Bundle
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.Message
import android.os.Messenger
import android.os.ParcelFileDescriptor
import org.json.JSONObject
import java.security.MessageDigest
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

/** Built into the signed APK. No downloaded code, Python runtime or host tools. */
class AndroidPyxelAdapter(private val context: Context) : AndroidVmAdapter {
    companion object {
        const val ID = "zdx.pyxel.android"
        const val DESCRIPTOR = "zdx.pyxel.android:v1:static-rgb8:1048576-pixels:32-rows:4096-steps:8MiB:64-512MiB:300s:no-chain:no-host"
        val MANIFEST_SHA256: String = MessageDigest.getInstance("SHA-256")
            .digest(DESCRIPTOR.toByteArray(Charsets.US_ASCII)).joinToString("") { "%02x".format(it) }
    }
    override val manifest = AndroidVmAdapterManifest(
        adapterId = ID, protocolVersion = ANDROID_VM_ADAPTER_PROTOCOL,
        artifactTypes = setOf("pyxel-frame-v1"), maxArtifactBytes = PyxelEngine.MAX_ARTIFACT.toLong(),
        maxMemoryMb = 512, maxRuntimeSeconds = 300,
        capabilities = setOf("pyxel-vm", "frame-hash", "isolated-process", PyxelEngine.FEATURE),
        manifestSha256 = MANIFEST_SHA256, signature = "apk-builtin"
    )

    override fun execute(request: AndroidVmExecutionRequest, cancellation: AndroidVmCancellation): JSONObject {
        require(Looper.myLooper() != Looper.getMainLooper()) { "VM execution must not block the main thread" }
        val task = request.task
        val metadata = task.optJSONObject("metadata") ?: JSONObject()
        require(metadata.optString("execution_model", "pixel-frame") == "pixel-frame") { "Android adapter does not support spatial tasks" }
        require(!metadata.has("frame_registry") && !metadata.has("opcode_map")) { "frame chaining and custom opcodes are unsupported" }
        require(request.artifact.canonicalFile.parentFile == request.privateArtifactDirectory.canonicalFile)
        val digest = task.getString("artifact_digest").lowercase()
        require(digest.matches(Regex("[0-9a-f]{64}"))) { "invalid artifact digest" }
        val threads = task.getInt("threads")
        require(threads in 1..PyxelEngine.MAX_THREADS)
        require(request.memoryLimitMb in 64..manifest.maxMemoryMb)
        val taskId = task.getString("task_id")
        require(taskId.length in 1..128)
        val result = AtomicReference<Bundle?>()
        val finished = CountDownLatch(1)
        val reply = Messenger(Handler(Looper.getMainLooper()) { message ->
            result.set(message.data)
            finished.countDown()
            true
        })
        val connection = object : ServiceConnection {
            override fun onServiceConnected(name: ComponentName, service: IBinder) {
                try {
                    ParcelFileDescriptor.open(request.artifact, ParcelFileDescriptor.MODE_READ_ONLY).use { descriptor ->
                        val data = Bundle().apply {
                            putParcelable("artifact", descriptor)
                            putString("digest", digest)
                            putString("task_id", taskId)
                            putInt("threads", threads)
                            putInt("memory_mb", request.memoryLimitMb)
                            putInt("max_seconds", request.maxRuntimeSeconds)
                        }
                        Messenger(service).send(Message.obtain().apply { what = 1; this.data = data; replyTo = reply })
                    }
                } catch (error: Exception) {
                    result.set(Bundle().apply { putString("error", error.message ?: "isolated VM IPC failed") })
                    finished.countDown()
                }
            }
            override fun onServiceDisconnected(name: ComponentName) { finished.countDown() }
            override fun onBindingDied(name: ComponentName) { finished.countDown() }
            override fun onNullBinding(name: ComponentName) { finished.countDown() }
        }
        cancellation.throwIfCancelled()
        val bound = context.bindService(Intent(context, PyxelIsolatedService::class.java), connection, Context.BIND_AUTO_CREATE)
        check(bound) { "isolated VM service could not start" }
        val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(request.maxRuntimeSeconds.toLong())
        try {
            while (!finished.await(100, TimeUnit.MILLISECONDS)) {
                cancellation.throwIfCancelled()
                if (System.nanoTime() >= deadline) throw AndroidVmExecutionTimeoutException("isolated VM exceeded deadline")
            }
            cancellation.throwIfCancelled()
            val response = result.get() ?: error("isolated VM process died")
            response.getString("error")?.let { throw IllegalArgumentException(it) }
            val encoded = requireNotNull(response.getString("result"))
            require(encoded.toByteArray(Charsets.UTF_8).size <= 64 * 1024) { "VM result exceeds limit" }
            return JSONObject(encoded).apply {
                require(getString("task_id") == taskId && getString("input_artifact_sha256") == digest)
                put("adapter_id", manifest.adapterId)
                put("adapter_protocol_version", manifest.protocolVersion)
                put("adapter_manifest_sha256", manifest.manifestSha256)
            }
        } finally {
            // onUnbind kills the dedicated isolated process, including native decode.
            context.unbindService(connection)
        }
    }
}
