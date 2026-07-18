package com.zerodrivex.zdxnode

/**
 * Converts hardware profile into a network-safe advertisement payload.
 */
class CapabilityAdvertisement {

    fun create(profile: NodeCapabilityProfile): Map<String, Any?> {
        return mapOf(
            "kind" to "capability",
            "node_id" to profile.nodeId,
            "cpu" to profile.cpu,
            "memory_mb" to profile.memoryMb,
            "gpu" to profile.gpu.model,
            "vulkan" to profile.gpu.vulkanSupported,
            "npu" to profile.npu.available,
            "nnapi" to profile.npu.nnapiVersion,
            "battery" to profile.batteryPercent,
            "charging" to profile.charging
        )
    }
}
