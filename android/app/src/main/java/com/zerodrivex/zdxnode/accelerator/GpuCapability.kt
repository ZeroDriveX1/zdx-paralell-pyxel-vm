package com.zerodrivex.zdxnode.accelerator

/**
 * GPU acceleration capability advertised by an Android node.
 */
data class GpuCapability(
    val vendor: String?,
    val model: String?,
    val vulkanSupported: Boolean,
    val openGlVersion: String?
)
