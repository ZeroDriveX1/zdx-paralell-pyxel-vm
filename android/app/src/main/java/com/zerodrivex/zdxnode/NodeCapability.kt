package com.zerodrivex.zdxnode

/**
 * Device capability model exchanged with ZDX network nodes.
 */
data class NodeCapability(
    val nodeId: String,
    val platform: String = "android",
    val cpu: String,
    val memoryMb: Int,
    val charging: Boolean,
    val batteryPercent: Int
)
