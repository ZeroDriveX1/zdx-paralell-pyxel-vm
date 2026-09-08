package com.zerodrivex.zdxnode

import org.json.JSONArray
import org.json.JSONObject
import java.io.File

const val ANDROID_VM_ADAPTER_PROTOCOL = 1

data class AndroidVmAdapterManifest(
    val adapterId: String, val protocolVersion: Int, val artifactTypes: Set<String>,
    val maxArtifactBytes: Long, val maxMemoryMb: Int, val maxRuntimeSeconds: Int,
    val capabilities: Set<String>, val manifestSha256: String, val signature: String
) {
    fun advertised(): JSONObject = JSONObject()
        .put("adapter_id", adapterId).put("protocol_version", protocolVersion)
        .put("artifact_types", JSONArray(artifactTypes.toList().sorted()))
        .put("max_artifact_bytes", maxArtifactBytes).put("max_memory_mb", maxMemoryMb)
        .put("max_runtime_seconds", maxRuntimeSeconds)
        .put("capabilities", JSONArray(capabilities.toList().sorted()))
        .put("manifest_sha256", manifestSha256).put("signature", signature)
}

data class AndroidVmExecutionRequest(
    val task: JSONObject, val artifact: File, val memoryLimitMb: Int,
    val maxRuntimeSeconds: Int, val privateArtifactDirectory: File
)

interface AndroidVmCancellation {
    fun isCancelled(): Boolean
    fun throwIfCancelled()
}

interface AndroidVmAdapter {
    val manifest: AndroidVmAdapterManifest
    fun execute(request: AndroidVmExecutionRequest, cancellation: AndroidVmCancellation): JSONObject
}

/** Registry intentionally contains no Pyxel adapter until one is signed and reviewed. */
class AndroidVmAdapterRegistry(private val adapters: Map<String, AndroidVmAdapter> = emptyMap()) {
    fun advertised(): JSONArray = JSONArray(adapters.values.map { it.manifest.advertised() })

    fun resolve(task: JSONObject): AndroidVmAdapter {
        val metadata = task.optJSONObject("metadata")
        val adapterId = metadata?.optString("android_vm_adapter_id", "")?.takeIf { it.isNotBlank() }
            ?: if (metadata?.optBoolean("android_bounded_probe", false) == true) "zdx.integrity-probe" else ""
        if (adapterId.isBlank()) throw UnsupportedOperationException("no Android VM adapter was requested")
        val adapter = adapters[adapterId] ?: throw UnsupportedOperationException("Android VM adapter is not installed")
        if (adapter.manifest.protocolVersion != ANDROID_VM_ADAPTER_PROTOCOL) {
            throw UnsupportedOperationException("Android VM adapter protocol mismatch")
        }
        return adapter
    }
}
