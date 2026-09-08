import json
import threading

import pytest

from zdx_ed25519_signer import ZDXEd25519Signer
from zdx_network import ZDXMessage
from zdx_node import ZDXNode
from zdx_server import ZDXServer
from zdx_auth_pipeline import AuthenticationError


def test_signed_message_covers_the_complete_envelope(tmp_path):
    signer = ZDXEd25519Signer("peer", key_path=str(tmp_path / "keys"))
    message = ZDXMessage(kind="heartbeat", payload={"value": 1}, peer_id="peer")
    signer.register_peer_public_key("peer", signer.get_public_key_pem())
    message.sign(signer)

    decoded = ZDXMessage.decode(message.encode()[4:])
    assert decoded.auth_payload() == message.auth_payload()
    assert signer.verify_self_signature(decoded.auth_payload(), decoded.signature)

    decoded.payload["value"] = 2
    assert not signer.verify_self_signature(decoded.auth_payload(), decoded.signature)


def test_secure_server_accepts_enrolled_signed_node(tmp_path):
    peer_signer = ZDXEd25519Signer("peer", key_path=str(tmp_path / "peer-keys"))
    server = ZDXServer(
        host="127.0.0.1",
        port=0,
        require_auth=True,
        trusted_peers={"peer": peer_signer.get_public_key_pem()},
        state_path=str(tmp_path / "state.json"),
    )
    thread = threading.Thread(target=server.serve, daemon=True)
    thread.start()

    try:
        for _ in range(20):
            if server.port:
                break
            threading.Event().wait(0.01)
        node = ZDXNode(host="127.0.0.1", port=server.port, node_id="peer", signer=peer_signer)
        sock = node.connect()
        try:
            assert node.ping(sock).kind == "heartbeat"
        finally:
            sock.close()
    finally:
        server.stop()
        thread.join(timeout=1)


def test_unknown_signed_identity_is_queued_for_operator_enrollment(tmp_path):
    peer_signer = ZDXEd25519Signer("pending-peer", key_path=str(tmp_path / "pending-keys"))
    server = ZDXServer(
        host="127.0.0.1",
        port=0,
        require_auth=True,
        trusted_peers={},
        state_path=str(tmp_path / "state.json"),
    )
    thread = threading.Thread(target=server.serve, daemon=True)
    thread.start()
    try:
        for _ in range(20):
            if server.port:
                break
            threading.Event().wait(0.01)
        node = ZDXNode(host="127.0.0.1", port=server.port, node_id="pending-peer", signer=peer_signer)
        with pytest.raises(ConnectionError):
            node.connect()
        pending = json.loads((tmp_path / "pending_enrollments.json").read_text())
        assert pending["pending-peer"]["public_key"] == peer_signer.get_public_key_pem()
        assert len(pending["pending-peer"]["public_key_sha256"]) == 64

        server.register_peer("pending-peer", peer_signer.get_public_key_pem())
        sock = node.connect()
        try:
            assert node.ping(sock).kind == "heartbeat"
        finally:
            sock.close()
    finally:
        server.stop()
        thread.join(timeout=1)


def test_secure_server_rejects_replayed_sequence(tmp_path):
    peer_signer = ZDXEd25519Signer("peer", key_path=str(tmp_path / "peer-keys"))
    server = ZDXServer(
        require_auth=True,
        trusted_peers={"peer": peer_signer.get_public_key_pem()},
        state_path=str(tmp_path / "state.json"),
    )
    node = ZDXNode(node_id="peer", signer=peer_signer)
    message = node.identity()

    server._authenticate(message)
    with pytest.raises(AuthenticationError) as error:
        server._authenticate(message)
    assert error.value.stage == "replay_protection"


def test_protocol_rejects_unknown_fields():
    with pytest.raises(ValueError, match="unknown message fields"):
        ZDXMessage.decode(
            b'{"kind":"heartbeat","payload":{},"unexpected":true}'
        )
