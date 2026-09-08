package com.zerodrivex.zdxnode

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

/** Stable identity and dynamic capability payloads sent after mesh connect. */
class ZdxProtocol(private val context: Context) {
    private val preferences = context.getSharedPreferences("zdx_identity", Context.MODE_PRIVATE)
    private val nodeId = preferences.getString("node_id", null) ?: UUID.randomUUID().toString().also {
        preferences.edit().putString("node_id", it).apply()
    }
    private val collector = DeviceCapabilityCollector(context)

    fun nodeId(): String = nodeId

    fun identity(): JSONObject = JSONObject()
        .put("kind", "identity").put("node_id", nodeId).put("platform", "android").put("protocol", 1)

    fun capabilityReportJson(): JSONObject {
        val capability = collector.collect()
        val policy = ResourcePolicyStore(context).load()
        val cpuCount = Runtime.getRuntime().availableProcessors().coerceAtLeast(1)
        val safeConcurrency = (cpuCount / 2).coerceIn(1, 4)
        return JSONObject()
            .put("node_id", nodeId).put("platform", "android").put("protocol", 1)
            .put("cpu_count", cpuCount).put("hardware", capability.cpu)
            .put("memory_mb", capability.memoryMb).put("available_memory_mb", capability.availableMemoryMb)
            .put("charging", capability.charging).put("battery_percent", capability.batteryPercent)
            .put("gpu", JSONObject().put("vulkan", capability.gpu.vulkanSupported).put("open_gl", capability.gpu.openGlVersion))
            .put("npu", JSONObject().put("available", capability.npu.available).put("nnapi_version", capability.npu.nnapiVersion))
            .put("vm_features", JSONArray().put("artifact-sha256").put("bounded-admission"))
            .put("android_vm_adapter_protocol", ANDROID_VM_ADAPTER_PROTOCOL)
            .put("android_vm_adapters", AndroidTaskExecutor(context).advertisedAdapters())
            .put("safe_limits", JSONObject()
                .put("memory_limit_mb", policy.memoryLimitMb.coerceAtMost((capability.memoryMb * 0.25).toInt().coerceAtLeast(128)))
                .put("min_free_memory_mb", policy.minFreeMemoryMb)
                .put("max_concurrent_tasks", safeConcurrency)
                .put("idle_only", policy.idleOnly)
                .put("require_charging", policy.requireCharging))
    }

    fun heartbeat(): JSONObject = JSONObject().put("kind", "heartbeat").put("node_id", nodeId)
}
