package com.zerodrivex.zdxnode

import com.zerodrivex.zdxnode.accelerator.GpuCapability
import com.zerodrivex.zdxnode.accelerator.NpuCapability

/** Device capability model exchanged with ZDX network nodes. */
data class NodeCapability(
    val nodeId: String,
    val platform: String = "android",
    val cpu: String,
    val memoryMb: Int,
    val availableMemoryMb: Int = 0,
    val charging: Boolean,
    val batteryPercent: Int,
    val gpu: GpuCapability = GpuCapability(null, null, false, null),
    val npu: NpuCapability = NpuCapability(false, 0, null)
)
