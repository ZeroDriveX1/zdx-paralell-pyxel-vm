import json
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from security.identity import NodeIdentity
from zdx_network import (
    MessageAuthenticator,
    ProtocolError,
    SessionRegistry,
    TrustedKeyStore,
    ZDXMessage,
)
from zdx_node_registry import ZDXNodeRegistry
from zdx_session import NodeCredentials, SessionClient, SessionCoordinator


def credentials():
    return NodeCredentials(NodeIdentity(Ed25519PrivateKey.generate()))


def established(ttl=900):
    coordinator = credentials()
    node = credentials()
    server_trust = TrustedKeyStore()
    server_trust.enroll(node.node_id, node.public_key_bytes)
    client_trust = TrustedKeyStore()
    client_trust.enroll(coordinator.node_id, coordinator.public_key_bytes)
    sessions = SessionRegistry(ttl)
    server = SessionCoordinator(coordinator, server_trust, sessions)
    client = SessionClient(node, client_trust)
    challenge = server.accept_hello(client.hello())
    confirmation = client.confirm(challenge, coordinator.node_id)
    acknowledgement = server.accept_confirmation(confirmation)
    client.accept_ack(acknowledgement)
    return coordinator, node, client, server, sessions, server_trust


def test_valid_mutual_authentication_and_packet_validation():
    _, node, client, _, sessions, trust = established()
    packet = client.message("heartbeat", {"status": "alive"})
    session = MessageAuthenticator(trust, sessions).validate(packet)
    assert session.peer_id == node.node_id
    assert session.last_sequence == 1


def test_invalid_signature_rejected_without_consuming_replay_state():
    _, _, client, _, sessions, trust = established()
    packet = client.message("heartbeat", {})
    packet.signature = packet.signature[:-2] + "AA"
    with pytest.raises(ProtocolError, match="signature"):
        MessageAuthenticator(trust, sessions).validate(packet)
    assert sessions.get(packet.session_id).last_sequence == 0


def test_expired_and_future_timestamps_rejected():
    _, _, client, _, sessions, trust = established()
    auth = MessageAuthenticator(trust, sessions)
    for stamp in (time.time() - 61, time.time() + 61):
        packet = client.credentials.message(
            "heartbeat", {}, session_id=client.session_id,
            sequence=1, timestamp=stamp,
        )
        with pytest.raises(ProtocolError, match="timestamp"):
            auth.validate(packet)


def test_replay_and_duplicate_nonce_rejected():
    _, _, client, _, sessions, trust = established()
    auth = MessageAuthenticator(trust, sessions)
    first = client.message("heartbeat", {})
    auth.validate(first)
    with pytest.raises(ProtocolError, match="sequence|nonce"):
        auth.validate(first)
    second = client.credentials.message(
        "heartbeat", {}, session_id=client.session_id,
        sequence=2, nonce=first.nonce,
    )
    with pytest.raises(ProtocolError, match="nonce"):
        auth.validate(second)


def test_sequence_gap_and_protocol_version_rejected():
    _, _, client, _, sessions, trust = established()
    auth = MessageAuthenticator(trust, sessions)
    gap = client.credentials.message(
        "heartbeat", {}, session_id=client.session_id, sequence=2
    )
    with pytest.raises(ProtocolError, match="expected 1"):
        auth.validate(gap)
    wrong_version = client.credentials.message(
        "heartbeat", {}, session_id=client.session_id, sequence=1, version=99
    )
    with pytest.raises(ProtocolError, match="protocol"):
        auth.validate(wrong_version)


def test_unknown_type_and_checksum_rejected():
    _, _, client, _, sessions, trust = established()
    auth = MessageAuthenticator(trust, sessions)
    unknown = client.credentials.message(
        "not-a-message", {}, session_id=client.session_id, sequence=1
    )
    with pytest.raises(ProtocolError, match="message_type"):
        auth.validate(unknown)
    bad_checksum = client.credentials.message(
        "heartbeat", {}, session_id=client.session_id, sequence=1,
        checksum="0" * 64,
    )
    with pytest.raises(ProtocolError, match="checksum"):
        auth.validate(bad_checksum)


def test_session_expiration_and_cleanup():
    sessions = SessionRegistry(ttl=1)
    session = sessions.establish("peer", now=100)
    with pytest.raises(ProtocolError, match="expired"):
        sessions.get(session.session_id, now=102)
    assert sessions.cleanup(now=102) == 0


def test_reconnect_replaces_old_session():
    sessions = SessionRegistry()
    first = sessions.establish("peer")
    second = sessions.establish("peer")
    with pytest.raises(ProtocolError, match="unknown"):
        sessions.get(first.session_id)
    assert sessions.get(second.session_id).peer_id == "peer"


def test_duplicate_node_id_and_session_binding_rejected():
    registry = ZDXNodeRegistry()
    registry.register("node", {}, "session-a", "1")
    with pytest.raises(ProtocolError, match="another key"):
        registry.register("node", {}, "session-b", "2")
    with pytest.raises(ProtocolError, match="another node"):
        registry.register("other", {}, "session-a", "1")


def test_registry_reconnect_heartbeat_and_stale_cleanup():
    registry = ZDXNodeRegistry(stale_after=10)
    registry.register("node", {"cpu": 1}, "old", now=100)
    record = registry.register("node", {"cpu": 2}, "new", now=105)
    assert record["reconnects"] == 1
    with pytest.raises(ProtocolError, match="not active"):
        registry.heartbeat("node", "old", now=106)
    assert registry.heartbeat("node", "new", now=106)
    assert registry.remove_stale(now=117) == ["node"]


def test_coordinator_recovery_uses_pinned_identity_not_session_state():
    coordinator = credentials()
    node = credentials()
    trust = TrustedKeyStore()
    trust.enroll(node.node_id, node.public_key_bytes)
    client_trust = TrustedKeyStore()
    client_trust.enroll(coordinator.node_id, coordinator.public_key_bytes)
    client = SessionClient(node, client_trust)
    recovered = SessionCoordinator(coordinator, trust, SessionRegistry())
    challenge = recovered.accept_hello(client.hello())
    ack = recovered.accept_confirmation(
        client.confirm(challenge, coordinator.node_id)
    )
    client.accept_ack(ack)
    assert ack.payload["authenticated"] is True


def test_malformed_packets_rejected():
    with pytest.raises(ProtocolError, match="JSON"):
        ZDXMessage.decode(b"{")
    body = json.dumps({"kind": "heartbeat"}).encode()
    with pytest.raises(ProtocolError, match="field mismatch"):
        ZDXMessage.decode(body)


def test_key_rotation_requires_authorization_and_version():
    identity = credentials()
    trust = TrustedKeyStore()
    trust.enroll(identity.node_id, identity.public_key_bytes)
    new_key = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    with pytest.raises(ProtocolError, match="not authorized"):
        trust.rotate(identity.node_id, new_key, "2", lambda *_: False)
    trust.rotate(identity.node_id, new_key, "2", lambda *_: True)
    assert trust.get(identity.node_id, "2")
