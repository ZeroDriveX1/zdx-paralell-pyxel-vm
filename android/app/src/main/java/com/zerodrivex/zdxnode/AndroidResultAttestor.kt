package com.zerodrivex.zdxnode

import android.content.Context
import android.os.Build
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.MessageDigest
import java.security.Signature

/** Signs the result claim with the same Keystore identity used by mesh envelopes. */
class AndroidResultAttestor(private val context: Context) {
    companion object { private const val KEY_ALIAS = "zdx-node-ed25519" }
    private val preferences = context.getSharedPreferences("zdx_identity", Context.MODE_PRIVATE)
    private val nodeId = preferences.getString("node_id", "") ?: ""
    private val keyPair: KeyPair by lazy { loadKeyPair() }

    fun attest(result: JSONObject): JSONObject {
        val claim = JSONObject()
            .put("attestation_version", 1)
            .put("node_id", nodeId)
            .put("result", result)
            .put("result_sha256", sha256(canonical(result)))
        return claim.put("attestation_signature", sign(canonical(claim)))
            .put("public_key", publicKeyPem())
    }

    private fun loadKeyPair(): KeyPair {
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

    private fun sign(value: String): String = Signature.getInstance("Ed25519").run {
        initSign(keyPair.private); update(value.toByteArray(Charsets.UTF_8)); Base64.encodeToString(sign(), Base64.NO_WRAP)
    }
    private fun publicKeyPem(): String = "-----BEGIN PUBLIC KEY-----\n${Base64.encodeToString(keyPair.public.encoded, Base64.NO_WRAP)}\n-----END PUBLIC KEY-----\n"
    private fun sha256(value: String): String = MessageDigest.getInstance("SHA-256").digest(value.toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it) }
    private fun canonical(value: JSONObject): String = canonicalValue(value)
    private fun canonicalValue(value: Any?): String = when (value) {
        null, JSONObject.NULL -> "null"
        is JSONObject -> value.keys().asSequence().toList().sorted().joinToString(prefix = "{", postfix = "}") { key -> JSONObject.quote(key) + ":" + canonicalValue(value.get(key)) }
        is JSONArray -> (0 until value.length()).joinToString(prefix = "[", postfix = "]") { canonicalValue(value.get(it)) }
        is String -> JSONObject.quote(value)
        is Boolean, is Number -> value.toString()
        else -> JSONObject.quote(value.toString())
    }
}
