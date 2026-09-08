package com.zerodrivex.zdxnode

import android.content.Context
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.ExecutionException
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException

class AndroidVmExecutionCancelledException(message: String) : Exception(message)
class AndroidVmExecutionTimeoutException(message: String) : Exception(message)

/** Executes only registered adapters inside strict private-file/time/resource boundaries. */
class AndroidTaskExecutor(private val context: Context) {
    private val privateDirectory = File(context.filesDir, "zdx-downloads").canonicalFile
    private val probe = IntegrityProbeAdapter()
    private val registry = AndroidVmAdapterRegistry(mapOf(probe.manifest.adapterId to probe))

    fun advertisedAdapters() = registry.advertised()

    fun execute(task: JSONObject, artifact: File, policy: ResourcePolicy): JSONObject {
        val adapter = registry.resolve(task)
        val request = AndroidVmExecutionRequest(
            task = task, artifact = artifact.canonicalFile, memoryLimitMb = policy.memoryLimitMb,
            maxRuntimeSeconds = task.optInt("max_seconds", 300).coerceIn(1, adapter.manifest.maxRuntimeSeconds),
            privateArtifactDirectory = privateDirectory
        )
        require(request.artifact.parentFile == privateDirectory) { "artifact is outside private Android storage" }
        require(request.artifact.length() <= adapter.manifest.maxArtifactBytes) { "artifact exceeds adapter limit" }
        require(request.memoryLimitMb <= adapter.manifest.maxMemoryMb) { "task exceeds adapter memory limit" }
        val admission = AndroidResourceAdmission(context)
        val executor = Executors.newSingleThreadExecutor()
        val future = executor.submit<JSONObject> {
            adapter.execute(request, object : AndroidVmCancellation {
                override fun isCancelled(): Boolean = !admission.canRun(ResourcePolicyStore(context).load()) || Thread.currentThread().isInterrupted
                override fun throwIfCancelled() {
                    if (isCancelled()) throw AndroidVmExecutionCancelledException("Android resource policy revoked execution")
                }
            })
        }
        return try {
            future.get(request.maxRuntimeSeconds.toLong(), TimeUnit.SECONDS)
        } catch (_: TimeoutException) {
            future.cancel(true)
            throw AndroidVmExecutionTimeoutException("Android adapter exceeded max runtime")
        } catch (error: ExecutionException) {
            val cause = error.cause
            when (cause) { is Exception -> throw cause; else -> throw RuntimeException(cause) }
        } finally {
            executor.shutdownNow()
        }
    }

    private class IntegrityProbeAdapter : AndroidVmAdapter {
        override val manifest = AndroidVmAdapterManifest(
            adapterId = "zdx.integrity-probe", protocolVersion = ANDROID_VM_ADAPTER_PROTOCOL,
            artifactTypes = setOf("opaque"), maxArtifactBytes = 8L * 1024L * 1024L,
            maxMemoryMb = 1024, maxRuntimeSeconds = 300,
            capabilities = setOf("artifact-sha256"), manifestSha256 = "builtin-integrity-probe-v1",
            signature = "platform-builtin"
        )

        override fun execute(request: AndroidVmExecutionRequest, cancellation: AndroidVmCancellation): JSONObject {
            val digest = MessageDigest.getInstance("SHA-256")
            val started = System.currentTimeMillis()
            request.artifact.inputStream().use { stream ->
                val buffer = ByteArray(64 * 1024)
                while (true) {
                    cancellation.throwIfCancelled()
                    val count = stream.read(buffer)
                    if (count < 0) break
                    digest.update(buffer, 0, count)
                }
            }
            val actual = digest.digest().joinToString("") { "%02x".format(it) }
            val expected = request.task.optString("artifact_digest", "").lowercase()
            require(expected.isNotBlank() && expected == actual) { "adapter artifact digest mismatch" }
            return JSONObject()
                .put("task_id", request.task.optString("task_id"))
                .put("status", "bounded_probe_verified")
                .put("verification_result", "artifact_sha256_verified")
                .put("artifact_sha256", actual)
                .put("input_artifact_sha256", actual)
                .put("adapter_id", manifest.adapterId)
                .put("adapter_protocol_version", manifest.protocolVersion)
                .put("adapter_manifest_sha256", manifest.manifestSha256)
                .put("completed_work", 1)
                .put("elapsed_ms", System.currentTimeMillis() - started)
        }
    }
}
