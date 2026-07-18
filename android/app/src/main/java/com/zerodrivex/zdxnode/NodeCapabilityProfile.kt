package com.zerodrivex.zdxnode

import com.zerodrivex.zdxnode.accelerator.GpuCapability
import com.zerodrivex.zdxnode.accelerator.NpuCapability

/**
 * Unified hardware profile advertised to ZDX scheduler.
 */
data class NodeCapabilityProfile(
    val nodeId: String,
    val cpu: String,
    val memoryMb: Int,
    val gpu: GpuCapability,
    val npu: NpuCapability,
    val batteryPercent: Int,
    val charging: Boolean
)
