import json
import multiprocessing
from pathlib import Path

from security.identity import NodeIdentity
from security.revocation import RevocationRegistry
from zdx_network import SessionRegistry, TrustedKeyStore
from zdx_node_registry import ZDXNodeRegistry
from zdx_pixel_memory.store import PixelStore
from zdx_state import ZDXState


def _pixel_write(directory, prefix, count):
    store = PixelStore(directory)
    for number in range(count):
        store.write(f"{prefix}-{number}", {"number": number})


def test_pixel_store_process_safe_index_and_values(tmp_path):
    directory = str(tmp_path / "memory")
    processes = [
        multiprocessing.Process(target=_pixel_write, args=(directory, prefix, 8))
        for prefix in ("a", "b", "c")
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    store = PixelStore(directory)
    assert len(store.keys()) == 24
    assert store.read("b-7") == {"number": 7}
    index = json.loads((Path(directory) / "keys.json").read_text())
    assert index["metadata"]["schema"] == "pixel-memory-index"


def test_pixel_store_reads_legacy_png_and_upgrades_on_write(tmp_path):
    from zdx_pixel_memory import codec
    directory = tmp_path / "memory"
    directory.mkdir()
    codec.encode({"old": True}).save(directory / "legacy.px.png")
    store = PixelStore(str(directory))
    assert store.read("legacy") == {"old": True}
    store.write("legacy", {"new": True})
    assert store.read("legacy") == {"new": True}


def test_pixel_corruption_is_quarantined(tmp_path):
    store = PixelStore(str(tmp_path))
    path = Path(store.write("key", {"valid": True}))
    store.write("key", {"second": True})
    path.write_bytes(b"not a PNG")
    assert store.read("key") == {"valid": True}
    assert list(tmp_path.glob("*.corrupt.*"))


def test_identity_key_and_metadata_survive_restart(tmp_path):
    path = tmp_path / "identity.pem"
    first = NodeIdentity.load_or_create(str(path))
    second = NodeIdentity.load_or_create(str(path))
    assert first.node_id == second.node_id
    assert path.stat().st_mode & 0o077 == 0
    metadata = json.loads((tmp_path / "identity.pem.metadata.json").read_text())
    assert metadata["data"]["node_id"] == first.node_id
    assert "private" not in json.dumps(metadata).lower()


def test_trust_revocation_session_and_registry_metadata_restart(tmp_path):
    identity = NodeIdentity.load_or_create(str(tmp_path / "peer.pem"))
    trust_path = tmp_path / "trust.json"
    trust = TrustedKeyStore(trust_path)
    trust.enroll(identity.node_id, identity.public_key_bytes)
    assert TrustedKeyStore(trust_path).is_enrolled(identity.node_id)

    revocation_path = tmp_path / "revocations.json"
    RevocationRegistry(revocation_path).revoke(identity.node_id)
    assert RevocationRegistry(revocation_path).is_revoked(identity.node_id)

    session_path = tmp_path / "sessions.json"
    sessions = SessionRegistry(path=session_path)
    session = sessions.establish(identity.node_id)
    recovered = SessionRegistry(path=session_path)
    assert recovered.recovered_metadata[0]["session_id"] == session.session_id
    assert recovered._sessions == {}  # restart requires fresh authentication

    registry_path = tmp_path / "registry.json"
    registry = ZDXNodeRegistry(path=registry_path)
    registry.register(identity.node_id, {"cpu": 1}, "session", "1")
    assert ZDXNodeRegistry(path=registry_path).get(identity.node_id)["capabilities"] == {
        "cpu": 1
    }


def test_coordinator_state_process_safe_restart(tmp_path):
    path = tmp_path / "coordinator.json"
    state = ZDXState(path)
    state.record_peer("node", {"trusted": True})
    state.record_heartbeat()
    recovered = ZDXState(path)
    assert recovered.data["heartbeats"] == 1
    assert "node" in recovered.data["peers"]
