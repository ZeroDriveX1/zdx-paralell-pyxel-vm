package com.zerodrivex.zdxnode

import java.util.UUID

/**
 * Android-side adapter for ZDX network messages.
 * Transport implementation can map this to TCP/TLS layer.
 */
class ZdxProtocol {
    private val nodeId = UUID.randomUUID().toString()

    fun identity(): Map<String, Any> {
        return mapOf(
            "kind" to "identity",
            "node_id" to nodeId,
            "platform" to "android",
            "protocol" to 1
        )
    }

    fun heartbeat(): Map<String, Any> {
        return mapOf(
            "kind" to "heartbeat",
            "node_id" to nodeId
        )
    }
}
