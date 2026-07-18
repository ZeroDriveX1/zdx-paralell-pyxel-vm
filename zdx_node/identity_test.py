from zdx_node.identity import NodeIdentity


def test_identity_signing(tmp_path):
    identity = NodeIdentity.load_or_create(str(tmp_path / "identity.json"))
    signature = identity.sign(b"heartbeat")

    assert identity.node_id
    assert identity.public_key()
    assert signature
