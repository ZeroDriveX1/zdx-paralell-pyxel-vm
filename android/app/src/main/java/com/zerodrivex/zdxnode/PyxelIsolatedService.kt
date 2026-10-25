package com.zerodrivex.zdxnode

import android.app.Service
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.os.Bundle
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.os.Message
import android.os.Messenger
import android.os.ParcelFileDescriptor
import android.os.Process
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.security.MessageDigest
import java.util.concurrent.TimeUnit

/** No app permissions, private files, identity keys or network in this process. */
class PyxelIsolatedService : Service() {
    private lateinit var worker: HandlerThread
    private lateinit var messenger: Messenger

    override fun onCreate() {
        super.onCreate()
        worker = HandlerThread("pyxel-isolated-worker").apply { start() }
        messenger = Messenger(Handler(worker.looper) { message ->
            if (message.what == 1) {
                val response = Bundle()
                try {
                    response.putString("result", execute(message.data).toString())
                } catch (error: Exception) {
                    response.putString("error", (error.message ?: "VM execution failed").take(512))
                }
                try { message.replyTo?.send(Message.obtain().apply { data = response }) }
                catch (_: Exception) { Process.killProcess(Process.myPid()) }
            }
            true
        })
    }

    override fun onBind(intent: Intent?): IBinder = messenger.binder

    override fun onUnbind(intent: Intent?): Boolean {
        Process.killProcess(Process.myPid())
        return false
    }

    override fun onDestroy() {
        worker.quitSafely()
        Process.killProcess(Process.myPid())
        super.onDestroy()
    }

    @Suppress("DEPRECATION")
    private fun execute(request: Bundle): JSONObject {
        check(checkSelfPermission(android.Manifest.permission.INTERNET) == android.content.pm.PackageManager.PERMISSION_DENIED) { "VM service must have no network permission" }
        val started = System.nanoTime()
        val seconds = request.getInt("max_seconds")
        require(seconds in 1..300)
        val deadline = started + TimeUnit.SECONDS.toNanos(seconds.toLong())
        val checkpoint = {
            if (System.nanoTime() >= deadline) throw AndroidVmExecutionTimeoutException("VM deadline exceeded")
        }
        val memoryMb = request.getInt("memory_mb")
        require(memoryMb in 64..512)
        val descriptor = requireNotNull(request.getParcelable<ParcelFileDescriptor>("artifact"))
        val data = ParcelFileDescriptor.AutoCloseInputStream(descriptor).use { stream ->
            val output = ByteArrayOutputStream()
            val buffer = ByteArray(64 * 1024)
            while (true) {
                checkpoint()
                val count = stream.read(buffer)
                if (count < 0) break
                require(count <= PyxelEngine.MAX_ARTIFACT - output.size()) { "artifact exceeds limit" }
                output.write(buffer, 0, count)
            }
            output.toByteArray()
        }
        val digest = MessageDigest.getInstance("SHA-256").digest(data).joinToString("") { "%02x".format(it) }
        require(digest == request.getString("digest")) { "adapter artifact digest mismatch" }
        val geometry = PyxelEngine.inspectPng(data, checkpoint)
        // Account conservatively for compressed copies, bitmap, pixel array and runtime reserve.
        require(data.size.toLong() * 3 + geometry.width.toLong() * geometry.height * 12 + 32L * 1024 * 1024 <= memoryMb.toLong() * 1024 * 1024) { "estimated decoder working set exceeds memory reservation" }
        checkpoint()
        val bitmap = requireNotNull(BitmapFactory.decodeByteArray(data, 0, data.size, BitmapFactory.Options().apply {
            inPreferredConfig = Bitmap.Config.ARGB_8888
            inScaled = false
        })) { "PNG decode failed" }
        val pixels: IntArray
        try {
            require(bitmap.width == geometry.width && bitmap.height == geometry.height) { "decoded geometry mismatch" }
            pixels = IntArray(geometry.width * geometry.height)
            bitmap.getPixels(pixels, 0, geometry.width, 0, 0, geometry.width, geometry.height)
        } finally { bitmap.recycle() }
        val state = PyxelEngine.execute(pixels, geometry.width, geometry.height, request.getInt("threads"), checkpoint)
        val registers = JSONObject()
        state.registers.forEachIndexed { index, values ->
            registers.put("T$index", JSONObject().put("A", values[0]).put("B", values[1]).put("OUT", values[2]).put("T", values[3]))
        }
        val shared = JSONObject()
        state.shared.forEachIndexed { index, value -> shared.put("M$index", value) }
        return JSONObject().put("task_id", request.getString("task_id"))
            .put("input_artifact_sha256", digest).put("artifact_sha256", digest)
            .put("execution_model", "pixel-frame").put("status", "completed")
            .put("executor_uid", Process.myUid())
            .put("verification_result", "artifact_sha256_verified").put("completed_work", 1)
            .put("registers", registers).put("shared", shared).put("clock", state.clock).put("column_steps", state.steps)
            .put("elapsed_ms", TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - started))
    }
}
