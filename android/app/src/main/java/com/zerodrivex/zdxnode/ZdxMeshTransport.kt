package com.zerodrivex.zdxnode

import android.content.Context
import android.os.Build
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.File
import java.io.IOException
import java.io.RandomAccessFile
import java.net.InetSocketAddress
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.MessageDigest
import java.security.Signature
import java.security.cert.CertificateFactory
import java.util.UUID
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocket
import javax.net.ssl.TrustManagerFactory

data class ZdxMeshConfig(
    val host: String,
    val port: Int,
    val enabled: Boolean = false,
    val caCertificatePem: String = ""
) {
    fun isConfigured(): Boolean = enabled && host.isNotBlank() && port in 1..65535
}

class ZdxMeshPreemptedException : IOException("Android resource policy no longer admits this transfer")

/** Authenticated Android implementation of the ZDX length-prefixed protocol. */
class ZdxMeshTransport(private val context: Context, private val config: ZdxMeshConfig) {
    companion object {
        private const val MAX_MESSAGE = 16 * 1024 * 1024
        private const val MAX_ANDROID_ARTIFACT = 8 * 1024 * 1024
        private const val CHUNK = 1024 * 1024
        private const val IDENTITY_PREFS = "zdx_identity"
        private const val KEY_ALIAS = "zdx-node-ed25519"
    }

    private val preferences = context.getSharedPreferences(IDENTITY_PREFS, Context.MODE_PRIVATE)
    private val nodeId = preferences.getString("node_id", null) ?: UUID.randomUUID().toString().also {
        preferences.edit().putString("node_id", it).apply()
    }
    private val keyPair: KeyPair by lazy { loadOrCreateKeyPair() }
    private var sequence = preferences.getLong("sequence", 1L)
    private var socket: SSLSocket? = null
    private var input: BufferedInputStream? = null
    private var output: BufferedOutputStream? = null
    private var lastCapability: JSONObject? = null

    fun isConfigured(): Boolean = config.enabled && config.host.isNotBlank() && config.port in 1..65535

    @Synchronized fun connect() {
        if (socket?.isConnected == true && socket?.isClosed == false) return
        check(isConfigured()) { "ZDX mesh endpoint is not configured" }
        val ssl = (buildSslContext().socketFactory.createSocket() as SSLSocket)
        ssl.connect(InetSocketAddress(config.host, config.port), 5_000)
        ssl.soTimeout = 15_000
        ssl.startHandshake()
        socket = ssl
        input = BufferedInputStream(ssl.inputStream)
        output = BufferedOutputStream(ssl.outputStream)
        val ack = request("identity", identityPayload())
        check(ack.optString("kind") == "identity_ack") { "mesh identity was rejected" }
    }

    @Synchronized fun close() {
        try { socket?.close() } finally {
            socket = null
            input = null
            output = null
        }
    }

    fun register(capability: JSONObject): JSONObject {
        connect()
        lastCapability = capability
        val report = request("capability_report", capability)
        check(report.optString("kind") == "capability_ack") { "capability registration was rejected" }
        val registration = request("compute_register", JSONObject().put("capabilities", capability))
        check(registration.optString("kind") == "compute_ack") { "compute registration was rejected" }
        return registration
    }

    fun poll(availableMemoryMb: Int, cpuCount: Int, cpuPercent: Double): JSONObject = request(
        "compute_poll", JSONObject().put("available_memory_mb", availableMemoryMb)
            .put("cpu_count", cpuCount).put("cpu_percent", cpuPercent)
    )

    fun release(taskId: String, reason: String): JSONObject = request(
        "compute_release", JSONObject().put("task_id", taskId).put("reason", reason)
    )

    fun fail(taskId: String, error: String): JSONObject = request(
        "compute_fail", JSONObject().put("task_id", taskId).put("error", error.take(4_000))
    )

    fun complete(taskId: String, result: JSONObject): JSONObject = request(
        "compute_result", JSONObject().put("task_id", taskId).put("result", result)
    )

    /** Resume a private checkpoint and verify the final SHA-256 before returning it. */
    fun receiveArtifact(taskId: String, digest: String, shouldContinue: () -> Boolean = { true }): File {
        require(taskId.isNotBlank() && digest.matches(Regex("[0-9a-fA-F]{64}"))) { "invalid artifact identity" }
        val directory = File(context.filesDir, "zdx-downloads").apply { mkdirs() }
        val part = File(directory, "$taskId.part")
        val metadataFile = File(directory, "$taskId.json")
        var offset = loadCheckpoint(part, metadataFile, digest)
        var retries = 0
        while (true) {
            try {
                connect()
                val ready = request("artifact_download_begin", JSONObject().put("task_id", taskId).put("digest", digest.lowercase()))
                check(ready.optString("kind") == "artifact_download_ready") { "artifact download was rejected" }
                val payload = ready.getJSONObject("payload")
                val token = payload.getString("token")
                val size = payload.getInt("size")
                require(size in 1..MAX_ANDROID_ARTIFACT) { "artifact exceeds Android limit" }
                if (offset > size || !checkpointMatches(metadataFile, digest, size)) {
                    part.delete(); metadataFile.delete(); offset = 0
                }
                saveCheckpoint(metadataFile, digest, size)
                RandomAccessFile(part, "rw").use { file ->
                    while (offset < size) {
                        if (!shouldContinue()) throw ZdxMeshPreemptedException()
                        val reply = request("artifact_download_chunk", JSONObject()
                            .put("task_id", taskId).put("digest", digest.lowercase())
                            .put("token", token).put("offset", offset))
                        val chunk = reply.getJSONObject("payload")
                        val data = Base64.decode(chunk.getString("data_b64"), Base64.DEFAULT)
                        check(chunk.getInt("offset") == offset && data.isNotEmpty() && offset + data.size <= size) {
                            "invalid artifact chunk"
                        }
                        file.seek(offset.toLong())
                        file.write(data)
                        file.fd.sync()
                        offset += data.size
                    }
                }
                check(part.length() == size.toLong() && sha256(part) == digest.lowercase()) {
                    part.delete(); metadataFile.delete(); "artifact SHA-256 verification failed"
                }
                metadataFile.delete()
                val finalFile = File(directory, "$taskId.verified")
                if (finalFile.exists()) finalFile.delete()
                check(part.renameTo(finalFile)) { "cannot finalize artifact checkpoint" }
                return finalFile
            } catch (preempted: ZdxMeshPreemptedException) {
                throw preempted
            } catch (error: IOException) {
                close()
                if (++retries > 5) throw error
                offset = if (part.exists()) part.length().toInt() else 0
                Thread.sleep((250L * retries).coerceAtMost(2_000L))
            }
        }
    }

    private fun loadCheckpoint(part: File, metadataFile: File, digest: String): Int {
        if (!part.exists() || !metadataFile.exists()) return 0
        return try {
            val checkpoint = JSONObject(metadataFile.readText())
            if (checkpoint.optString("digest") == digest.lowercase() && checkpoint.optLong("size") >= part.length()) part.length().toInt()
            else { part.delete(); metadataFile.delete(); 0 }
        } catch (_: Exception) {
            part.delete(); metadataFile.delete(); 0
        }
    }

    private fun checkpointMatches(file: File, digest: String, size: Int): Boolean = try {
        val value = JSONObject(file.readText())
        value.optString("digest") == digest.lowercase() && value.optInt("size") == size
    } catch (_: Exception) { false }

    private fun saveCheckpoint(file: File, digest: String, size: Int) {
        file.writeText(JSONObject().put("digest", digest.lowercase()).put("size", size).toString())
    }

    private fun identityPayload(): JSONObject = JSONObject()
        .put("node_id", nodeId).put("protocol", 1).put("platform", "android")
        .put("public_key", publicKeyPem()).put("key_version", 1)

    @Synchronized private fun request(kind: String, payload: JSONObject): JSONObject {
        val current = sequence++
        preferences.edit().putLong("sequence", sequence).apply()
        val envelope = JSONObject().put("kind", kind).put("payload", payload)
            .put("request_id", UUID.randomUUID().toString()).put("version", 1)
            .put("timestamp", System.currentTimeMillis() / 1000.0).put("peer_id", nodeId)
            .put("sequence", current)
        envelope.put("signature", sign(canonical(envelope)))
        val bytes = envelope.toString().toByteArray(Charsets.UTF_8)
        require(bytes.size in 1..MAX_MESSAGE)
        val out = output ?: error("mesh is not connected")
        out.write((bytes.size ushr 24) and 0xff); out.write((bytes.size ushr 16) and 0xff)
        out.write((bytes.size ushr 8) and 0xff); out.write(bytes.size and 0xff)
        out.write(bytes); out.flush()
        val stream = input ?: error("mesh is not connected")
        val header = ByteArray(4); readFully(stream, header)
        val size = ((header[0].toInt() and 255) shl 24) or ((header[1].toInt() and 255) shl 16) or
            ((header[2].toInt() and 255) shl 8) or (header[3].toInt() and 255)
        require(size in 1..MAX_MESSAGE)
        val response = ByteArray(size); readFully(stream, response)
        return JSONObject(String(response, Charsets.UTF_8))
    }

    private fun buildSslContext(): SSLContext {
        if (config.caCertificatePem.isBlank()) return SSLContext.getDefault()
        val certificate = CertificateFactory.getInstance("X.509").generateCertificate(
            config.caCertificatePem.byteInputStream()
        )
        val anchors = KeyStore.getInstance(KeyStore.getDefaultType()).apply {
            load(null, null); setCertificateEntry("zdx-ca", certificate)
        }
        val trust = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm()).apply { init(anchors) }
        return SSLContext.getInstance("TLS").apply { init(null, trust.trustManagers, null) }
    }

    private fun loadOrCreateKeyPair(): KeyPair {
        check(Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) { "Android Keystore Ed25519 requires API 28 or newer" }
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        if (!store.containsAlias(KEY_ALIAS)) {
            KeyPairGenerator.getInstance("Ed25519", "AndroidKeyStore").apply {
                initialize(android.security.keystore.KeyGenParameterSpec.Builder(
                    KEY_ALIAS, android.security.keystore.KeyProperties.PURPOSE_SIGN
                ).build())
                generateKeyPair()
            }
        }
        return KeyPair(store.getCertificate(KEY_ALIAS).publicKey, store.getKey(KEY_ALIAS, null) as java.security.PrivateKey)
    }

    private fun publicKeyPem(): String = "-----BEGIN PUBLIC KEY-----\n${Base64.encodeToString(keyPair.public.encoded, Base64.NO_WRAP)}\n-----END PUBLIC KEY-----\n"
    private fun sign(value: String): String = Signature.getInstance("Ed25519").run {
        initSign(keyPair.private); update(value.toByteArray(Charsets.UTF_8)); Base64.encodeToString(sign(), Base64.NO_WRAP)
    }
    private fun sha256(file: File): String = MessageDigest.getInstance("SHA-256").run {
        file.inputStream().use { stream -> val buffer = ByteArray(CHUNK); while (true) { val count = stream.read(buffer); if (count < 0) break; update(buffer, 0, count) } }
        digest().joinToString("") { "%02x".format(it) }
    }
    private fun readFully(input: BufferedInputStream, target: ByteArray) { var offset = 0; while (offset < target.size) { val count = input.read(target, offset, target.size - offset); if (count < 0) throw IOException("mesh connection closed"); offset += count } }
    private fun canonical(value: JSONObject): String = when (value) {
        else -> canonicalValue(value)
    }
    private fun canonicalValue(value: Any?): String = when (value) {
        null, JSONObject.NULL -> "null"
        is JSONObject -> value.keys().asSequence().toList().sorted().joinToString(prefix = "{", postfix = "}") { key -> JSONObject.quote(key) + ":" + canonicalValue(value.get(key)) }
        is JSONArray -> (0 until value.length()).joinToString(prefix = "[", postfix = "]") { canonicalValue(value.get(it)) }
        is String -> JSONObject.quote(value)
        is Boolean, is Number -> value.toString()
        else -> JSONObject.quote(value.toString())
    }
}
