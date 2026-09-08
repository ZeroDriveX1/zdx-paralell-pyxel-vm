package com.zerodrivex.zdxnode

import android.content.Context

data class MeshSettings(
    val host: String = "",
    val port: Int = 8765,
    val enabled: Boolean = false,
    val caCertificatePem: String = ""
) {
    fun transportConfig() = ZdxMeshConfig(host.trim(), port, enabled, caCertificatePem.trim())
}

class MeshSettingsStore(context: Context) {
    private val preferences = context.getSharedPreferences("zdx_mesh", Context.MODE_PRIVATE)

    fun load(): MeshSettings = MeshSettings(
        host = preferences.getString("host", "") ?: "",
        port = preferences.getInt("port", 8765),
        enabled = preferences.getBoolean("enabled", false),
        caCertificatePem = preferences.getString("ca_certificate_pem", "") ?: ""
    )

    fun save(settings: MeshSettings) {
        require(settings.port in 1..65535)
        preferences.edit()
            .putString("host", settings.host.trim())
            .putInt("port", settings.port)
            .putBoolean("enabled", settings.enabled)
            .putString("ca_certificate_pem", settings.caCertificatePem.trim())
            .apply()
    }
}
