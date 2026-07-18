package com.zerodrivex.zdxnode.accelerator

import android.content.Context

/**
 * Hardware acceleration discovery layer.
 *
 * Future implementation will query Vulkan, OpenGL ES and NNAPI.
 */
class AcceleratorDetector(private val context: Context) {

    fun gpu(): GpuCapability {
        return GpuCapability(
            vendor = null,
            model = null,
            vulkanSupported = false,
            openGlVersion = null
        )
    }

    fun npu(): NpuCapability {
        return NpuCapability(
            available = false,
            nnapiVersion = 0,
            acceleratorName = null
        )
    }
}
