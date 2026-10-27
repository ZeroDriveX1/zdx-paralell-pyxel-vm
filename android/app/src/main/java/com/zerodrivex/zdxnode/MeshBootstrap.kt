package com.zerodrivex.zdxnode

import org.json.JSONObject
import java.io.IOException
import java.net.URL
import javax.net.ssl.HttpsURLConnection

/** The APK signature anchors the bootstrap URL; platform TLS authenticates it. */
class MeshBootstrap {
    companion object {
        const val URL = "https://zerodrivex.com/.well-known/zdx/cluster.json"
        const val CLUSTER_ID = "zdx-private-prod"
        private const val MAX_BYTES = 65536

        fun parse(text: String, now: Long = System.currentTimeMillis() / 1000): List<ZdxMeshConfig> {
            require(text.toByteArray(Charsets.UTF_8).size <= MAX_BYTES) { "directory is too large" }
            val manifest = JSONObject(text)
            require(manifest.getInt("version") == 1 && manifest.getString("cluster_id") == CLUSTER_ID) { "wrong mesh directory" }
            val expires = manifest.getLong("expires_at")
            require(expires > now && expires <= now + 180) { "mesh directory expired or clock is incorrect" }
            val entries = manifest.getJSONArray("coordinators")
            require(entries.length() in 1..100) { "no active coordinators" }
            return (0 until entries.length()).map { index ->
                val item = entries.getJSONObject(index)
                val host = item.getString("host")
                require(host.length in 1..253 && host.matches(Regex("[a-z0-9.-]+"))) { "invalid coordinator hostname" }
                val port = item.getInt("port")
                require(port in 1..65535 && item.getString("path") == "/zdx/mesh") { "invalid coordinator endpoint" }
                require(item.getLong("expires_at") > now && item.getLong("expires_at") <= now + 330) { "coordinator lease expired" }
                ZdxMeshConfig(host, port, true, "", "/zdx/mesh")
            }.distinct()
        }
    }

    private var nextEndpoint = 0

    fun resolve(): ZdxMeshConfig {
        val connection = java.net.URL(URL).openConnection() as HttpsURLConnection
        connection.instanceFollowRedirects = false
        connection.connectTimeout = 5000
        connection.readTimeout = 5000
        connection.setRequestProperty("Accept", "application/json")
        try {
            check(connection.responseCode == 200) { "mesh discovery unavailable (${connection.responseCode})" }
            require(connection.contentLengthLong <= MAX_BYTES) { "directory is too large" }
            val raw = connection.inputStream.use { input ->
                val out = java.io.ByteArrayOutputStream()
                val buffer = ByteArray(4096)
                while (true) {
                    val count = input.read(buffer)
                    if (count < 0) break
                    if (out.size() + count > MAX_BYTES) throw IOException("directory is too large")
                    out.write(buffer, 0, count)
                }
                out.toString("UTF-8")
            }
            val endpoints = parse(raw)
            return endpoints[Math.floorMod(nextEndpoint++, endpoints.size)]
        } finally {
            connection.disconnect()
        }
    }
}
