import threading
import time

import pytest

from security.identity import NodeIdentity
from zdx_fault_proxy import TCPFaultProxy
from zdx_network import TrustedKeyStore
from zdx_node import ZDXNode
from zdx_server import ZDXServer
from zdx_session import NodeCredentials
from zdx_tls import TLSConfig

from test_tls import _certificates


def _deployment(tmp_path):
    certificates = _certificates(tmp_path)
    coordinator = NodeCredentials(NodeIdentity.load_or_create(
        str(tmp_path / "coordinator.pem")
    ))
    worker = NodeCredentials(NodeIdentity.load_or_create(
        str(tmp_path / "worker.pem")
    ))
    trust = TrustedKeyStore()
    trust.enroll(worker.node_id, worker.public_key_bytes)
    server = ZDXServer(
        host="127.0.0.1", port=0, credentials=coordinator, trust=trust,
        state_path=str(tmp_path / "state.json"),
        tls_config=TLSConfig(
            certificates["ca"], certificates["server_cert"],
            certificates["server_key"],
        ),
    )
    thread = threading.Thread(target=server.serve, daemon=True)
    thread.start()
    deadline = time.time() + 2
    while server.port == 0 and time.time() < deadline:
        time.sleep(0.01)
    return certificates, coordinator, worker, server, thread


def _node(proxy, certificates, coordinator, worker):
    node = ZDXNode(
        host="127.0.0.1", port=proxy.port, credentials=worker,
        tls_config=TLSConfig(
            certificates["ca"], certificates["client_cert"],
            certificates["client_key"],
        ), server_hostname="localhost",
    )
    node.trust_coordinator(coordinator.node_id, coordinator.public_key_bytes)
    return node


def test_latency_jitter_partition_and_reauthentication(tmp_path):
    certificates, coordinator, worker, server, thread = _deployment(tmp_path)
    proxy = TCPFaultProxy(
        "127.0.0.1", server.port, latency=0.003, jitter=0.004
    ).start()
    node = _node(proxy, certificates, coordinator, worker)
    connection = node.connect(timeout=3)
    assert node.ping(connection).kind == "heartbeat_ack"
    proxy.partition()
    with pytest.raises((OSError, ConnectionError)):
        node.ping(connection)
    connection.close()

    recovered = _node(proxy, certificates, coordinator, worker)
    with recovered.connect(timeout=3) as connection:
        assert recovered.ping(connection).kind == "heartbeat_ack"
    proxy.stop(); server.stop(); thread.join(3)


@pytest.mark.parametrize("fault", [
    {"drop_every": 2},
    {"duplicate_every": 2},
    {"reorder_every": 2},
])
def test_corrupting_stream_faults_fail_closed_then_clean_reconnect(
    tmp_path, fault
):
    certificates, coordinator, worker, server, thread = _deployment(tmp_path)
    faulty = TCPFaultProxy("127.0.0.1", server.port, **fault).start()
    node = _node(faulty, certificates, coordinator, worker)
    with pytest.raises((OSError, ConnectionError, TimeoutError)):
        connection = node.connect(timeout=0.5)
        node.ping(connection)
    faulty.stop()

    clean = TCPFaultProxy("127.0.0.1", server.port).start()
    recovered = _node(clean, certificates, coordinator, worker)
    with recovered.connect(timeout=3) as connection:
        assert recovered.ping(connection).kind == "heartbeat_ack"
    clean.stop(); server.stop(); thread.join(3)
