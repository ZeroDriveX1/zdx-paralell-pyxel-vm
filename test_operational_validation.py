import json
import os
import random
import threading
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from security.identity import NodeIdentity
from zdx_failure import FailureInjector, InjectedFailure
from zdx_metrics import METRICS, MetricsRegistry
from zdx_network import (
    MessageAuthenticator,
    ProtocolError,
    SessionRegistry,
    TrustedKeyStore,
    ZDXMessage,
)
from zdx_node_registry import ZDXNodeRegistry
from zdx_pixel_memory.store import PixelStore
from zdx_session import NodeCredentials, SessionClient, SessionCoordinator
from zdx_storage import (
    CorruptStateError,
    Migration,
    MigrationError,
    MigrationRegistry,
    StateStore,
    atomic_write_bytes,
)
from zdx_validation import run_benchmarks, run_soak


def _credentials():
    return NodeCredentials(NodeIdentity(Ed25519PrivateKey.generate()))


def _authenticated(ttl=900):
    coordinator, node = _credentials(), _credentials()
    server_trust, client_trust = TrustedKeyStore(), TrustedKeyStore()
    server_trust.enroll(node.node_id, node.public_key_bytes)
    client_trust.enroll(coordinator.node_id, coordinator.public_key_bytes)
    sessions = SessionRegistry(ttl)
    server = SessionCoordinator(coordinator, server_trust, sessions)
    client = SessionClient(node, client_trust)
    challenge = server.accept_hello(client.hello())
    client.accept_ack(server.accept_confirmation(
        client.confirm(challenge, coordinator.node_id)
    ))
    return coordinator, node, client, sessions, server_trust


def test_metrics_json_export_and_required_fields(tmp_path):
    metrics = MetricsRegistry()
    metrics.increment("persistence_commits")
    metrics.increment("failed_commits")
    metrics.increment("recovery_operations")
    metrics.increment("migrations")
    metrics.increment("lock_contention")
    metrics.increment("transaction_retries")
    metrics.gauge("active_sessions", 2)
    metrics.gauge("connected_nodes", 3)
    metrics.observe("commit_latency", 0.01)
    metrics.observe("scheduler_latency", 0.02)
    path = tmp_path / "metrics.json"
    metrics.export_json(path)
    result = json.loads(path.read_text())
    assert result["counters"]["persistence_commits"] == 1
    assert result["gauges"] == {"active_sessions": 2, "connected_nodes": 3}
    assert result["latencies"]["commit_latency"]["average_seconds"] == 0.01
    assert result["resources"]["active_threads"] >= 1
    assert result["uptime_seconds"] >= 0


@pytest.mark.parametrize("point", [
    "commit.after_temp_fsync", "commit.after_backup", "commit.before_replace",
])
def test_deterministic_commit_failures_roll_back(point, tmp_path):
    path = tmp_path / "state.json"
    StateStore(path, "failure").save({"generation": 1})
    store = StateStore(
        path, "failure", failure_injector=FailureInjector({point: 1})
    )
    with pytest.raises(InjectedFailure, match=point):
        store.save({"generation": 2})
    assert StateStore(path, "failure").load() == {"generation": 1}
    assert not list(tmp_path.glob("*.tmp"))


def test_registry_failure_recovers_from_persistent_generation(tmp_path):
    path = tmp_path / "registry.json"
    stable = ZDXNodeRegistry(path=path)
    stable.register("node-a", {}, "session-a")
    injected = ZDXNodeRegistry(
        path=path,
        failure_injector=FailureInjector({"registry.before_persist": 1}),
    )
    with pytest.raises(InjectedFailure):
        injected.register("node-b", {}, "session-b")
    recovered = ZDXNodeRegistry(path=path)
    assert recovered.get("node-a")
    assert recovered.get("node-b") is None


def test_network_reconnect_reorder_duplicate_delay_and_restart(tmp_path):
    coordinator, node, client, sessions, trust = _authenticated()
    auth = MessageAuthenticator(trust, sessions)
    first = client.message("heartbeat", {})
    auth.validate(first)
    with pytest.raises(ProtocolError):
        auth.validate(first)  # duplicate
    reordered = node.message(
        "heartbeat", {}, session_id=client.session_id, sequence=3
    )
    with pytest.raises(ProtocolError, match="expected 2"):
        auth.validate(reordered)
    delayed = node.message(
        "heartbeat", {}, session_id=client.session_id, sequence=2,
        timestamp=time.time() - 120,
    )
    with pytest.raises(ProtocolError, match="timestamp"):
        auth.validate(delayed)
    sessions.close(client.session_id)

    trust_path = tmp_path / "trust.json"
    persistent_trust = TrustedKeyStore(trust_path)
    persistent_trust.enroll(node.node_id, node.public_key_bytes)
    rebuilt = TrustedKeyStore(trust_path)
    for _ in range(20):
        replacement = SessionRegistry(ttl=0.05)
        session = replacement.establish(node.node_id)
        replacement.close(session.session_id)
    assert rebuilt.is_enrolled(node.node_id)


def test_session_expiration_heartbeat_recovery_and_registry_rebuild(tmp_path):
    path = tmp_path / "registry.json"
    registry = ZDXNodeRegistry(stale_after=1, path=path)
    registry.register("node", {}, "session", now=100)
    assert registry.heartbeat("node", "session", now=100.5)
    assert registry.remove_stale(now=102) == ["node"]
    assert ZDXNodeRegistry(stale_after=1, path=path).get("node") is None
    sessions = SessionRegistry(ttl=0.01)
    session = sessions.establish("node")
    with pytest.raises(ProtocolError, match="expired"):
        sessions.get(session.session_id, now=session.expires_at + 1)


def test_fuzz_network_packets_fail_closed():
    randomizer = random.Random(1300)
    for _ in range(500):
        raw = randomizer.randbytes(randomizer.randrange(0, 512))
        try:
            ZDXMessage.decode(raw)
        except (ProtocolError, UnicodeError, ValueError, TypeError):
            pass


def test_fuzz_json_transaction_and_identity_records(tmp_path):
    randomizer = random.Random(1301)
    for number in range(100):
        path = tmp_path / f"state-{number}.json"
        raw = randomizer.randbytes(randomizer.randrange(1, 256))
        path.write_bytes(raw)
        try:
            StateStore(path, "fuzz").load()
        except CorruptStateError:
            pass
        assert not path.exists()
        assert list(tmp_path.glob(f"state-{number}.json.corrupt.*"))
    identity = tmp_path / "identity.pem"
    identity.write_bytes(b"not-a-private-key")
    with pytest.raises((ValueError, TypeError)):
        NodeIdentity.load_or_create(str(identity))
    assert identity.read_bytes() == b"not-a-private-key"


def test_fuzz_migration_and_png_metadata_fail_closed(tmp_path):
    registry = MigrationRegistry()

    def require_mapping(value):
        if not isinstance(value, dict):
            raise ValueError("mapping required")
        return {**value, "migrated": True}

    registry.register(Migration("fuzz", 1, 2, require_mapping))
    for malformed in (None, [], "text", 1, True):
        with pytest.raises(MigrationError):
            registry.upgrade("fuzz", malformed, 1, 2)
    pixels = PixelStore(str(tmp_path / "pixels"))
    for malformed in (None, [], "text", {"metadata": []}):
        with pytest.raises(CorruptStateError):
            pixels._unwrap({"__zdx_persistent__": malformed})


def test_repeated_corruption_recovery_and_checksum_under_load(tmp_path):
    path = tmp_path / "state.json"
    store = StateStore(path, "recovery")
    for generation in range(25):
        store.save({"generation": generation})
        store.save({"generation": generation + 1})
        document = json.loads(path.read_text())
        document["data"]["generation"] = -1
        path.write_text(json.dumps(document))
        recovered = store.load()
        assert recovered["generation"] == generation
        store.save({"generation": generation + 1})


def test_bounded_soak_detects_no_resource_leak():
    result = run_soak(iterations=12)
    assert result["passed"], result
    assert result["descriptor_growth"] == 0
    assert result["thread_growth"] == 0
    assert result["stale_transactions"] == 0


def test_benchmarks_execute_and_are_machine_readable():
    result = run_benchmarks(iterations=3)
    names = {item["name"] for item in result["benchmarks"]}
    assert names == {
        "vm_execution", "persistence_commit", "scheduler_selection",
        "registry_lookup", "coordinator_authenticated_dispatch",
        "ed25519_sign", "ed25519_verify", "png_serialization",
        "state_migration",
    }
    assert all(item["operations_per_second"] > 0 for item in result["benchmarks"])
