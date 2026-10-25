package com.zerodrivex.zdxnode

import android.os.Process
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.security.MessageDigest

@RunWith(AndroidJUnit4::class)
class AndroidPyxelAdapterTest {
    private val cancellation = object : AndroidVmCancellation {
        override fun isCancelled() = false
        override fun throwIfCancelled() = Unit
    }

    @Test fun canonicalDesktopPngExecutesInPermissionlessIsolatedProcess() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val directory = File(context.filesDir, "zdx-downloads").apply { mkdirs() }
        val artifact = File(directory, "android-instrumentation.png")
        artifact.writeBytes(instrumentation.context.assets.open("program.png").use { it.readBytes() })
        val digest = MessageDigest.getInstance("SHA-256").digest(artifact.readBytes()).joinToString("") { "%02x".format(it) }
        val task = JSONObject().put("task_id", "android-test").put("artifact_digest", digest).put("threads", 1)
            .put("metadata", JSONObject().put("android_vm_adapter_id", AndroidPyxelAdapter.ID))
        try {
            val request = AndroidVmExecutionRequest(task, artifact, 128, 30, directory)
            val result = AndroidPyxelAdapter(context).execute(request, cancellation)
            assertEquals(12, result.getJSONObject("shared").getInt("M0"))
            assertEquals(digest, result.getString("input_artifact_sha256"))
            assertEquals(AndroidPyxelAdapter.MANIFEST_SHA256, result.getString("adapter_manifest_sha256"))
            assertNotEquals(Process.myUid(), result.getInt("executor_uid"))
            assertEquals("completed", result.getString("status"))
            // Exercise the real Keystore provider as well as byte-level signing.
            ZdxProtocol(context).nodeId()
            val claim = AndroidResultAttestor(context).attest(result.put("interop", "é😀"))
            val signature = android.util.Base64.decode(claim.getString("attestation_signature"), android.util.Base64.NO_WRAP)
            val pem = claim.getString("public_key").replace("-----BEGIN PUBLIC KEY-----", "").replace("-----END PUBLIC KEY-----", "")
            val store = java.security.KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
            val key = store.getCertificate("zdx-node-ed25519").publicKey
            assertArrayEquals(key.encoded, android.util.Base64.decode(pem, android.util.Base64.DEFAULT))
            assertEquals(64, signature.size)
            // Android's Keystore provider does not import Ed25519 X.509 keys.
            // CI exports this public claim and verifies it using the actual Python
            // coordinator verifier, rather than relying on a second Android signer.
            android.util.Log.i("ZDXInterop", "CLAIM=" + android.util.Base64.encodeToString(
                claim.toString().toByteArray(Charsets.UTF_8), android.util.Base64.NO_WRAP
            ))
        } finally { artifact.delete() }
    }
}
