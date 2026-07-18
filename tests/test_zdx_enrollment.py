from zdx_enrollment_handshake import ZDXEnrollmentHandshake
from zdx_node_identity import ZDXNodeIdentity


def test_enrollment_request():
    handshake = ZDXEnrollmentHandshake()
    identity = ZDXNodeIdentity("/tmp/zdx-test-identity.json")

    result = handshake.request(identity, {"cpu_count": 4})

    assert result["status"] == "pending"
    assert "identity_hash" in result["capabilities"]
