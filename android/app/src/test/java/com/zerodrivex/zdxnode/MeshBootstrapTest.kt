package com.zerodrivex.zdxnode

import org.junit.Assert.*
import org.junit.Test

class MeshBootstrapTest {
    private fun manifest(expires: Long = 1120, host: String = "zerodrivex.com", cluster: String = MeshBootstrap.CLUSTER_ID): String =
        """{"version":1,"cluster_id":"$cluster","expires_at":$expires,"coordinators":[{"node_id":"a","host":"$host","port":443,"path":"/zdx/mesh","expires_at":1300}]}"""

    @Test fun discoversHttpsEndpointWithoutPrivateCa() {
        val config = MeshBootstrap.parse(manifest(), 1000).single()
        assertEquals("zerodrivex.com", config.host)
        assertEquals(443, config.port)
        assertEquals("/zdx/mesh", config.upgradePath)
        assertEquals("", config.caCertificatePem)
    }

    @Test fun rejectsExpiredWrongClusterAndHeaderInjection() {
        for (raw in listOf(manifest(expires = 999), manifest(cluster = "attacker"), manifest(host = "evil\\r\\nHost: bad"), manifest(expires = 9000))) {
            try { MeshBootstrap.parse(raw, 1000); fail("invalid directory was accepted") }
            catch (_: IllegalArgumentException) { }
        }
    }
}
