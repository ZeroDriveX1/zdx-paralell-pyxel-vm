import multiprocessing
import queue
import threading
import time

from security.identity import NodeIdentity
from zdx_network import TrustedKeyStore
from zdx_node import ZDXNode
from zdx_server import ZDXServer
from zdx_session import NodeCredentials
from zdx_tls import TLSConfig

from test_tls import _certificates


def _worker(
    host, port, key_path, capabilities, coordinator_id,
    coordinator_public_key, certificates, stop_event, ready_queue,
):
    node = ZDXNode(
        host=host, port=port, key_path=key_path, capabilities=capabilities,
        tls_config=TLSConfig(
            certificates["ca"], certificates["client_cert"],
            certificates["client_key"],
        ),
        server_hostname="localhost",
    )
    node.trust_coordinator(coordinator_id, coordinator_public_key)
    try:
        with node.connect(timeout=3) as connection:
            ready_queue.put(("joined", node.node_id))
            while not stop_event.wait(0.05):
                node.ping(connection)
    except Exception as exc:
        ready_queue.put(("error", f"{type(exc).__name__}: {exc}"))


def _wait_for(predicate, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("timed out waiting for distributed state")


def test_three_worker_process_join_leave_rejoin_and_redistribution(tmp_path):
    certificates = _certificates(tmp_path)
    coordinator = NodeCredentials(NodeIdentity.load_or_create(
        str(tmp_path / "coordinator-app.pem")
    ))
    worker_specs = []
    trust = TrustedKeyStore(tmp_path / "trust.json")
    for number, cpu in enumerate((2, 4, 8)):
        key_path = tmp_path / f"worker-{number}.pem"
        identity = NodeIdentity.load_or_create(str(key_path))
        trust.enroll(identity.node_id, identity.public_key_bytes)
        worker_specs.append((str(key_path), identity.node_id, {"cpu_count": cpu}))

    server = ZDXServer(
        host="127.0.0.1", port=0, credentials=coordinator, trust=trust,
        state_path=str(tmp_path / "coordinator.json"),
        tls_config=TLSConfig(
            certificates["ca"], certificates["server_cert"],
            certificates["server_key"],
        ),
    )
    server_thread = threading.Thread(target=server.serve, daemon=True)
    server_thread.start()
    _wait_for(lambda: server.port)

    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    workers = []
    for key_path, node_id, capabilities in worker_specs:
        stop = context.Event()
        process = context.Process(target=_worker, args=(
            "127.0.0.1", server.port, key_path, capabilities,
            coordinator.node_id, coordinator.public_key_bytes,
            certificates, stop, ready,
        ))
        process.start()
        workers.append((process, stop, node_id))

    joined = set()
    for _ in workers:
        status, value = ready.get(timeout=8)
        assert status == "joined", value
        joined.add(value)
    assert joined == {spec[1] for spec in worker_specs}
    _wait_for(lambda: len([
        item for item in server.registry.all_nodes().values()
        if item["session_id"]
    ]) == 3)

    strongest = worker_specs[2][1]
    assert server.select_worker()[0] == strongest
    workers[2][1].set()
    workers[2][0].join(5)
    assert workers[2][0].exitcode == 0
    _wait_for(lambda: server.select_worker()[0] != strongest)
    assert server.select_worker()[0] == worker_specs[1][1]

    rejoin_stop = context.Event()
    rejoin = context.Process(target=_worker, args=(
        "127.0.0.1", server.port, worker_specs[2][0], worker_specs[2][2],
        coordinator.node_id, coordinator.public_key_bytes,
        certificates, rejoin_stop, ready,
    ))
    rejoin.start()
    status, value = ready.get(timeout=8)
    assert (status, value) == ("joined", strongest)
    _wait_for(lambda: server.select_worker()[0] == strongest)

    for process, stop, _ in workers[:2]:
        stop.set()
        process.join(5)
        assert process.exitcode == 0
    rejoin_stop.set()
    rejoin.join(5)
    assert rejoin.exitcode == 0
    _wait_for(lambda: server.select_worker() is None)
    server.stop()
    server_thread.join(3)


def test_coordinator_restart_and_node_reauthentication(tmp_path):
    certificates = _certificates(tmp_path)
    coordinator = NodeCredentials(NodeIdentity.load_or_create(
        str(tmp_path / "coordinator-app.pem")
    ))
    worker_identity = NodeIdentity.load_or_create(str(tmp_path / "worker.pem"))
    trust_path = tmp_path / "trust.json"
    trust = TrustedKeyStore(trust_path)
    trust.enroll(worker_identity.node_id, worker_identity.public_key_bytes)

    def start(port):
        instance = ZDXServer(
            host="127.0.0.1", port=port, credentials=coordinator,
            trust=TrustedKeyStore(trust_path),
            state_path=str(tmp_path / "state.json"),
            tls_config=TLSConfig(
                certificates["ca"], certificates["server_cert"],
                certificates["server_key"],
            ),
        )
        thread = threading.Thread(target=instance.serve, daemon=True)
        thread.start()
        _wait_for(lambda: instance.port)
        return instance, thread

    server, thread = start(0)
    port = server.port
    node = ZDXNode(
        host="127.0.0.1", port=port,
        credentials=NodeCredentials(worker_identity),
        tls_config=TLSConfig(
            certificates["ca"], certificates["client_cert"],
            certificates["client_key"],
        ), server_hostname="localhost",
    )
    node.trust_coordinator(coordinator.node_id, coordinator.public_key_bytes)
    with node.connect() as connection:
        assert node.ping(connection).kind == "heartbeat_ack"
    server.stop(); thread.join(3)

    restarted, restarted_thread = start(port)
    recovered = ZDXNode(
        host="127.0.0.1", port=port,
        credentials=NodeCredentials(worker_identity),
        tls_config=node.tls_config, server_hostname="localhost",
    )
    recovered.trust_coordinator(
        coordinator.node_id, coordinator.public_key_bytes
    )
    with recovered.connect() as connection:
        assert recovered.ping(connection).kind == "heartbeat_ack"
    restarted.stop(); restarted_thread.join(3)
