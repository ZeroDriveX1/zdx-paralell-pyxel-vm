import multiprocessing
import os
import signal
import time

from zdx_node_registry import ZDXNodeRegistry
from zdx_storage import Migration, MigrationRegistry, StateStore


def _commit_then_wait(path, generation, ready):
    store = StateStore(path, "power-loss")
    ready.set()
    store.save({"generation": generation, "payload": "x" * 4_000_000})


def _migrate_then_wait(path, entered):
    registry = MigrationRegistry()

    def migration(data):
        entered.set()
        time.sleep(30)
        return {**data, "migrated": True}

    registry.register(Migration("power-migration", 1, 2, migration))
    StateStore(
        path, "power-migration", version=2, migrations=registry
    ).load()


def _registry_update(path, entered):
    registry = ZDXNodeRegistry(path=path)
    entered.set()
    registry.register(
        "new-node", {"blob": "x" * 4_000_000}, "new-session"
    )


def _kill(process):
    os.kill(process.pid, signal.SIGKILL)
    process.join(5)
    assert process.exitcode == -signal.SIGKILL


def test_hard_kill_during_repeated_commits_recovers_automatically(tmp_path):
    path = str(tmp_path / "state.json")
    StateStore(path, "power-loss").save({"generation": 0})
    context = multiprocessing.get_context("spawn")
    for generation in range(1, 11):
        ready = context.Event()
        process = context.Process(
            target=_commit_then_wait, args=(path, generation, ready)
        )
        process.start()
        assert ready.wait(5)
        time.sleep(0.002)
        _kill(process)
        recovered = StateStore(path, "power-loss").load()
        assert recovered["generation"] in (generation - 1, generation)
        StateStore(path, "power-loss").save({"generation": generation})
    assert StateStore(path, "power-loss").load()["generation"] == 10
    assert not list(tmp_path.glob("*.tmp"))


def test_hard_kill_during_migration_preserves_source(tmp_path):
    path = str(tmp_path / "migration.json")
    StateStore(path, "power-migration", version=1).save({"version": 1})
    context = multiprocessing.get_context("spawn")
    entered = context.Event()
    process = context.Process(target=_migrate_then_wait, args=(path, entered))
    process.start()
    assert entered.wait(5)
    _kill(process)
    assert StateStore(
        path, "power-migration", version=1
    ).load() == {"version": 1}


def test_hard_kill_during_registry_update_leaves_valid_generation(tmp_path):
    path = str(tmp_path / "registry.json")
    registry = ZDXNodeRegistry(path=path)
    registry.register("stable-node", {}, "stable-session")
    context = multiprocessing.get_context("spawn")
    entered = context.Event()
    process = context.Process(target=_registry_update, args=(path, entered))
    process.start()
    assert entered.wait(5)
    time.sleep(0.002)
    _kill(process)
    recovered = ZDXNodeRegistry(path=path)
    assert recovered.get("stable-node")
    if recovered.get("new-node"):
        assert recovered.get("new-node")["session_id"] == "new-session"
