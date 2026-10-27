import json
import socket
import threading
from pathlib import Path

import pytest

from zdx_bootstrap import (BootstrapHTTPServer, CoordinatorDirectory, DIRECTORY_PATH,
                           MESH_PATH, REGISTER_PATH, announcement)
from zdx_ed25519_signer import ZDXEd25519Signer


@pytest.fixture
def directory(tmp_path):
    signer = ZDXEd25519Signer('coordinator-a', str(tmp_path / 'keys'))
    endpoint = {'host': 'zerodrivex.com', 'port': 443, 'path': MESH_PATH}
    (tmp_path / 'coordinators.json').write_text(json.dumps({signer.node_id: {
        'public_key': signer.get_public_key_pem(), 'endpoint': endpoint}}))
    return CoordinatorDirectory(tmp_path, 'test', clock=lambda: 1000), signer, endpoint


def test_signed_lease_survives_restart_and_expires(directory):
    registry, signer, endpoint = directory
    registry.register(announcement(signer, 'test', endpoint, now=1000))
    restarted = CoordinatorDirectory(registry.root, 'test', clock=lambda: 1001)
    assert restarted.manifest()['coordinators'][0]['host'] == 'zerodrivex.com'
    restarted.clock = lambda: 1301
    assert restarted.manifest()['coordinators'] == []


def test_replay_tampering_unapproved_endpoint_and_wrong_cluster(directory):
    registry, signer, endpoint = directory
    valid = announcement(signer, 'test', endpoint, now=1000)
    registry.register(valid)
    with pytest.raises(ValueError, match='replay'):
        registry.register(valid)
    valid['payload']['endpoint']['host'] = 'attacker.example'
    with pytest.raises(PermissionError):
        registry.register(valid)
    with pytest.raises(PermissionError):
        registry.register(announcement(signer, 'test', {**endpoint, 'host': 'attacker.example'}, now=1001))
    with pytest.raises(PermissionError):
        registry.register(announcement(signer, 'other', endpoint, now=1001))


def test_revocation_removes_discovery_immediately(directory):
    registry, signer, endpoint = directory
    registry.register(announcement(signer, 'test', endpoint, now=1000))
    (registry.root / 'coordinators.json').write_text('{}')
    assert registry.manifest()['coordinators'] == []
    with pytest.raises(PermissionError):
        registry.register(announcement(signer, 'test', endpoint, now=1001))


def test_unsigned_unknown_and_expired_announcements_rejected(directory, tmp_path):
    registry, signer, endpoint = directory
    rogue = ZDXEd25519Signer('rogue', str(tmp_path / 'rogue'))
    for message in [announcement(rogue, 'test', endpoint, now=1000),
                    {'payload': announcement(signer, 'test', endpoint, now=1000)['payload'], 'signature': ''}]:
        with pytest.raises(PermissionError):
            registry.register(message)
    for now in [1, 1100]:
        with pytest.raises(ValueError):
            registry.register(announcement(signer, 'test', endpoint, now=now))


class EchoMesh:
    running = True

    def handle_client(self, connection, address):
        # Exercise the exact byte boundary after HTTP Upgrade, including NULs.
        data = connection.recv(8)
        connection.sendall(data)


def test_gateway_preserves_binary_stream_and_bounds_registration(directory):
    registry, signer, endpoint = directory
    registry.register(announcement(signer, 'test', endpoint, now=1000))
    http = BootstrapHTTPServer(('127.0.0.1', 0), registry, EchoMesh())
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    from http.client import HTTPConnection
    try:
        client = HTTPConnection(*http.server_address, timeout=2)
        client.request('GET', DIRECTORY_PATH)
        response = client.getresponse()
        assert response.status == 200
        assert json.loads(response.read())['coordinators']
        client.close()
        client = HTTPConnection(*http.server_address, timeout=2)
        client.request('POST', REGISTER_PATH, body='{}', headers={'Content-Length': '999999'})
        assert client.getresponse().status == 400
        client.close()
        with socket.create_connection(http.server_address, timeout=2) as sock:
            sock.sendall(f'GET {MESH_PATH} HTTP/1.1\r\nHost: localhost\r\nConnection: Upgrade\r\nUpgrade: zdx-mesh/1\r\n\r\n'.encode())
            header = b''
            while not header.endswith(b'\r\n\r\n'):
                header += sock.recv(1)
            assert header.startswith(b'HTTP/1.1 101')
            sock.sendall(b'\x00\x00\x00\x04test')
            assert sock.recv(8) == b'\x00\x00\x00\x04test'
    finally:
        http.shutdown()
        http.server_close()
        thread.join()


def test_gateway_refuses_public_bind(directory):
    registry, _, _ = directory
    with pytest.raises(ValueError, match='loopback'):
        BootstrapHTTPServer(('0.0.0.0', 0), registry, EchoMesh())

@pytest.mark.parametrize('kind', ['cluster_state_pull', 'cluster_state_push', 'compute_submit', 'artifact_put'])
def test_worker_enrollment_does_not_grant_coordinator_or_submission_role(tmp_path, kind):
    from zdx_bootstrap import EnrolledGatewayServer
    from zdx_network import ZDXMessage
    worker = ZDXEd25519Signer('worker', str(tmp_path / 'worker-keys'))
    peers = {'worker': worker.get_public_key_pem()}
    peers_file = tmp_path / 'peers.json'
    peers_file.write_text(json.dumps(peers))
    (tmp_path / 'coordinators.json').write_text('{}')
    server = EnrolledGatewayServer(peers_file=peers_file, require_auth=True,
        trusted_peers=peers, state_path=str(tmp_path / 'node.json'),
        compute_state_path=str(tmp_path / 'compute.json'), artifact_root=str(tmp_path / 'artifacts'),
        cluster_state_path=str(tmp_path / 'cluster.json'))
    message = ZDXMessage(kind=kind, payload={}, peer_id='worker', sequence=1).sign(worker)
    with pytest.raises(PermissionError):
        server._authenticate(message)


def test_oversized_identity_rejected_before_pending_storage(tmp_path):
    from zdx_bootstrap import EnrolledGatewayServer
    from zdx_network import ZDXMessage
    peers = tmp_path / 'peers.json'; peers.write_text('{}')
    server = EnrolledGatewayServer(peers_file=peers, state_path=str(tmp_path / 'node.json'),
        compute_state_path=str(tmp_path / 'compute.json'), artifact_root=str(tmp_path / 'artifacts'),
        cluster_state_path=str(tmp_path / 'cluster.json'))
    message = ZDXMessage(kind='identity', payload={}, peer_id='x' * 129)
    with pytest.raises(PermissionError, match='identity length'):
        server._queue_pending_enrollment(None, message, ('127.0.0.1', 1))
    assert not (tmp_path / 'pending_enrollments.json').exists()
