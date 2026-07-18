package com.zerodrivex.zdxnode.accelerator

/**
 * Neural accelerator capability through Android NNAPI.
 */
data class NpuCapability(
    val available: Boolean,
    val nnapiVersion: Int,
    val acceleratorName: String?
)
