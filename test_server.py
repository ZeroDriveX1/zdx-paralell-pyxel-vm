import socket
import threading
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from security.identity import NodeIdentity
from zdx_network import TrustedKeyStore, recv_message, send_message
from zdx_server import ZDXServer
from zdx_session import NodeCredentials, SessionClient


def credentials():
    return NodeCredentials(NodeIdentity(Ed25519PrivateKey.generate()))


def test_server_authenticated_startup(tmp_path):
    coordinator = credentials()
    node = credentials()
    server_trust = TrustedKeyStore()
    server_trust.enroll(node.node_id, node.public_key_bytes)
    server = ZDXServer(
        host="127.0.0.1",
        port=0,
        credentials=coordinator,
        trust=server_trust,
        state_path=str(tmp_path / "state.json"),
        allow_insecure=True,
    )
    thread = threading.Thread(target=server.serve, daemon=True)
    thread.start()
    deadline = time.time() + 2
    while server.port == 0 and time.time() < deadline:
        time.sleep(0.01)

    client_trust = TrustedKeyStore()
    client_trust.enroll(coordinator.node_id, coordinator.public_key_bytes)
    auth = SessionClient(node, client_trust)
    client = socket.create_connection(("127.0.0.1", server.port), timeout=2)
    send_message(client, auth.hello())
    send_message(client, auth.confirm(recv_message(client), coordinator.node_id))
    auth.accept_ack(recv_message(client))
    send_message(client, auth.message(
        "identity", {"node_id": node.node_id, "capabilities": {"cpu": 2}}
    ))
    response = recv_message(client)

    assert response.kind == "identity_ack"
    assert response.payload["accepted"] is True
    assert response.signature
    server.stop()
    client.close()
    thread.join(timeout=2)


def test_server_rejects_unsigned_packet(tmp_path):
    server = ZDXServer(
        host="127.0.0.1", port=0, credentials=credentials(),
        state_path=str(tmp_path / "state.json"),
        allow_insecure=True,
    )
    server.running = True
    server_side, client_side = socket.socketpair()
    thread = threading.Thread(
        target=server.handle_client, args=(server_side, ("local", 0)), daemon=True
    )
    thread.start()
    from zdx_network import ZDXMessage
    send_message(client_side, ZDXMessage(kind="heartbeat", payload={}))
    thread.join(timeout=2)
    assert not thread.is_alive()
    client_side.close()
