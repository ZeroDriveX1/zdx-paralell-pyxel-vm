package com.zerodrivex.zdxnode

/**
 * Runtime status displayed by Android node UI.
 */
data class NodeStatus(
    val connected: Boolean,
    val peerCount: Int,
    val lastHeartbeat: Long,
    val framesVerified: Int
)
