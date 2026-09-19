import json
import multiprocessing
import os
import threading
import time

import pytest

import zdx_storage
from zdx_storage import (
    CorruptStateError,
    FileLock,
    LockTimeout,
    Migration,
    MigrationError,
    MigrationRegistry,
    StateStore,
    atomic_write_bytes,
)


def _increment(path, count):
    store = StateStore(path, "counter")
    for _ in range(count):
        store.update(lambda value: {"count": value["count"] + 1}, {"count": 0})


def test_versioned_envelope_and_legacy_upgrade(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"legacy": True}))
    store = StateStore(path, "example")
    assert store.load() == {"legacy": True}
    document = json.loads(path.read_text())
    metadata = document["metadata"]
    assert metadata["schema_version"] == 1
    assert metadata["created_at"]
    assert metadata["updated_at"]
    assert metadata["checksum"]
    assert metadata["compatibility"]["format"] == "zdx-state"


def test_checksum_corruption_quarantined_and_backup_recovered(tmp_path):
    path = tmp_path / "state.json"
    store = StateStore(path, "example")
    store.save({"generation": 1})
    store.save({"generation": 2})
    document = json.loads(path.read_text())
    document["data"]["generation"] = 999
    path.write_text(json.dumps(document))

    assert store.load() == {"generation": 1}
    assert store.last_recovery["recovered_from"].endswith(".bak")
    assert list(tmp_path.glob("state.json.corrupt.*"))


def test_corrupt_state_without_backup_is_not_silently_accepted(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{broken")
    with pytest.raises(CorruptStateError):
        StateStore(path, "example").load()
    assert not path.exists()
    assert list(tmp_path.glob("state.json.corrupt.*"))


def test_interrupted_temporary_write_never_becomes_visible(tmp_path):
    path = tmp_path / "state.json"
    store = StateStore(path, "example")
    store.save({"committed": True})
    (tmp_path / ".state.json.dead.tmp").write_bytes(b'{"partial":')
    assert store.load() == {"committed": True}


def test_atomic_write_rolls_back_when_commit_replace_fails(tmp_path, monkeypatch):
    path = tmp_path / "value.bin"
    atomic_write_bytes(path, b"old")
    real_replace = zdx_storage.os.replace

    def fail_commit(source, destination):
        if str(source).endswith(".tmp") and str(destination) == str(path):
            raise OSError("simulated crash")
        return real_replace(source, destination)

    monkeypatch.setattr(zdx_storage.os, "replace", fail_commit)
    with pytest.raises(OSError, match="simulated"):
        atomic_write_bytes(path, b"new")
    assert path.read_bytes() == b"old"


def test_process_safe_concurrent_writers(tmp_path):
    path = str(tmp_path / "counter.json")
    processes = [
        multiprocessing.Process(target=_increment, args=(path, 20))
        for _ in range(4)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    assert StateStore(path, "counter").load() == {"count": 80}


def test_concurrent_readers_never_observe_partial_state(tmp_path):
    path = tmp_path / "state.json"
    store = StateStore(path, "example")
    store.save({"value": 0})
    errors = []

    def reader():
        for _ in range(50):
            try:
                value = StateStore(path, "example").load()
                assert isinstance(value["value"], int)
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(4)]
    for thread in threads:
        thread.start()
    for value in range(25):
        store.save({"value": value})
    for thread in threads:
        thread.join()
    assert errors == []


def test_lock_timeout_and_stale_metadata_recovery(tmp_path):
    path = tmp_path / "state.lock"
    first = FileLock(path, timeout=1).acquire()
    try:
        with pytest.raises(LockTimeout):
            FileLock(path, timeout=0.05).acquire()
    finally:
        first.release()
    path.write_text('{"pid":999999,"acquired_at":"old"}')
    with FileLock(path, timeout=1):
        metadata = json.loads(path.read_text())
        assert metadata["pid"] == os.getpid()


def test_sequential_migration_and_log(tmp_path):
    path = tmp_path / "state.json"
    registry = MigrationRegistry()
    registry.register(Migration(
        "example", 1, 2, lambda data: {**data, "two": True}, name="add-two"
    ))
    StateStore(path, "example", version=1).save({"one": True})
    upgraded = StateStore(
        path, "example", version=2, migrations=registry
    )
    assert upgraded.load() == {"one": True, "two": True}
    log = json.loads(path.read_text())["metadata"]["migrations"]
    assert log[-1]["name"] == "add-two"


def test_legacy_state_can_upgrade_through_registered_versions(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"one": True}))
    registry = MigrationRegistry()
    registry.register(Migration(
        "example", 1, 2, lambda data: {**data, "two": True}
    ))
    assert StateStore(
        path, "example", version=2, migrations=registry
    ).load() == {"one": True, "two": True}


def test_migration_failure_retains_original_generation(tmp_path):
    path = tmp_path / "state.json"
    StateStore(path, "example", version=1).save({"one": True})
    original = path.read_bytes()
    registry = MigrationRegistry()

    def broken(_data):
        raise ValueError("broken migration")

    registry.register(Migration("example", 1, 2, broken))
    with pytest.raises(MigrationError, match="original state retained"):
        StateStore(path, "example", version=2, migrations=registry).load()
    assert path.read_bytes() == original


def test_newer_schema_is_rejected(tmp_path):
    path = tmp_path / "state.json"
    StateStore(path, "example", version=2).save({"future": True})
    with pytest.raises(MigrationError, match="newer"):
        StateStore(path, "example", version=1).load()
