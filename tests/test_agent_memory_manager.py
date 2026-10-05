import pytest

from pyxel_registry import PyxelRegistry
from zdx_agent_memory_manager import (
    MEMORY_ROOT_KEY,
    MemoryNamespacePolicy,
    MemoryQuotaExceeded,
)
from zdx_agent_runtime import ZDXAgentRuntime
from zdx_pixel_memory import ZDXAgentMemory
from zdx_spatial_frame import SpatialCompiler, SpatialLayout, SpatialPyxelVM, SpatialRegion


def _runtime(tmp_path, *, width=96, height=64):
    layout = SpatialLayout(
        width=width,
        height=height,
        execution_rows=1,
        regions=(SpatialRegion("memory", 0, 1, width, height - 1),),
    )
    path = str(tmp_path / "agent-memory.png")
    SpatialCompiler(layout).compile([["HALT"]], path)
    memory = ZDXAgentMemory(
        agent_id="memory",
        spatial=True,
        spatial_path=path,
        spatial_layout=layout,
        spatial_region="memory",
    )
    registry = PyxelRegistry()
    registry.register("vm", SpatialPyxelVM(layout=layout))
    registry.register("memory", memory)
    return path, layout, memory, ZDXAgentRuntime(registry, checkpoint_interval=10)


def test_namespaced_memory_isolated_resident_and_persists_restart(tmp_path):
    path, layout, memory, runtime = _runtime(tmp_path)
    before = open(path, "rb").read()
    manager = runtime.memory_manager(path)

    manager.set("facts", "model", {"name": "pyxel", "version": 1})
    manager.append("episodic", {"event": "boot"})
    manager.set("working", "scratch", [1, 2, 3])

    assert manager.get("facts", "model")["name"] == "pyxel"
    assert manager.snapshot("working") == {"scratch": [1, 2, 3]}
    assert len(manager.keys("episodic")) == 1
    assert open(path, "rb").read() == before

    runtime.checkpoint(path, barrier=True)
    runtime.close()

    reopened_memory = ZDXAgentMemory(
        agent_id="memory-reopen",
        spatial=True,
        spatial_path=path,
        spatial_layout=layout,
        spatial_region="memory",
    )
    registry = PyxelRegistry()
    registry.register("vm", SpatialPyxelVM(layout=layout))
    registry.register("memory", reopened_memory)
    reopened = ZDXAgentRuntime(registry)
    restored = reopened.memory_manager(path)

    assert restored.get("facts", "model") == {"name": "pyxel", "version": 1}
    assert restored.snapshot("working") == {"scratch": [1, 2, 3]}
    assert list(restored.snapshot("episodic").values()) == [{"event": "boot"}]
    reopened.close()


def test_reject_quota_is_atomic(tmp_path):
    path, _layout, _memory, runtime = _runtime(tmp_path)
    policies = {
        "facts": MemoryNamespacePolicy(
            "facts", max_bytes=4096, max_entries=2, eviction="reject"
        )
    }
    manager = runtime.memory_manager(path, policies=policies)
    manager.set("facts", "a", 1)
    manager.set("facts", "b", 2)
    before = manager.snapshot("facts")

    with pytest.raises(MemoryQuotaExceeded, match="quota exceeded"):
        manager.set("facts", "c", 3)

    assert manager.snapshot("facts") == before
    assert manager.get("facts", "c") is None
    runtime.close()


def test_fifo_quota_evicts_oldest_write_deterministically(tmp_path):
    path, _layout, _memory, runtime = _runtime(tmp_path)
    policies = {
        "working": MemoryNamespacePolicy(
            "working", max_bytes=4096, max_entries=2, eviction="fifo"
        )
    }
    manager = runtime.memory_manager(path, policies=policies)
    manager.set("working", "a", "one")
    manager.set("working", "b", "two")
    manager.set("working", "c", "three")

    assert manager.snapshot("working") == {"b": "two", "c": "three"}

    # Updating b makes it the newest write. Adding d must evict c.
    manager.set("working", "b", "two-updated")
    manager.set("working", "d", "four")
    assert manager.snapshot("working") == {"b": "two-updated", "d": "four"}
    runtime.close()


def test_full_document_capacity_is_checked_before_mutation(tmp_path):
    path, _layout, _memory, runtime = _runtime(tmp_path, width=32, height=16)
    policies = {
        "facts": MemoryNamespacePolicy(
            "facts", max_bytes=100_000, max_entries=8, eviction="reject"
        )
    }
    manager = runtime.memory_manager(path, policies=policies)
    before = manager.snapshot("facts")

    with pytest.raises(MemoryQuotaExceeded, match="persistent region capacity"):
        manager.set("facts", "oversized", b"x" * 20_000)

    assert manager.snapshot("facts") == before
    runtime.close()


def test_persisted_policy_mismatch_fails_closed(tmp_path):
    path, layout, memory, runtime = _runtime(tmp_path)
    original = {
        "facts": MemoryNamespacePolicy(
            "facts", max_bytes=2048, max_entries=8, eviction="reject"
        )
    }
    runtime.memory_manager(path, policies=original).set("facts", "a", 1)
    runtime.checkpoint(path, barrier=True)
    runtime.close()

    registry = PyxelRegistry()
    registry.register("vm", SpatialPyxelVM(layout=layout))
    registry.register(
        "memory",
        ZDXAgentMemory(
            agent_id="memory-policy-reopen",
            spatial=True,
            spatial_path=path,
            spatial_layout=layout,
            spatial_region="memory",
        ),
    )
    reopened = ZDXAgentRuntime(registry)
    changed = {
        "facts": MemoryNamespacePolicy(
            "facts", max_bytes=4096, max_entries=8, eviction="reject"
        )
    }
    with pytest.raises(ValueError, match="policies do not match"):
        reopened.memory_manager(path, policies=changed)
    reopened.close()


def test_malformed_namespaced_memory_root_fails_closed(tmp_path):
    path, _layout, _memory, runtime = _runtime(tmp_path)
    session = runtime.open_spatial_session(path)
    session.values[MEMORY_ROOT_KEY] = {
        "version": 999,
        "next_sequence": 1,
        "policies": {},
        "namespaces": {},
    }

    with pytest.raises(ValueError, match="schema version"):
        session.memory_manager()

    runtime.close(flush=False)


def test_usage_reports_encoded_quota_consumption(tmp_path):
    path, _layout, _memory, runtime = _runtime(tmp_path)
    manager = runtime.memory_manager(path)
    manager.set("facts", "alpha", {"value": 1})
    usage = manager.usage("facts")["facts"]

    assert usage["entries"] == 1
    assert 0 < usage["encoded_bytes"] <= usage["max_bytes"]
    assert usage["max_entries"] >= 1
    assert usage["eviction"] == "reject"
    runtime.close()


def test_resident_policy_metadata_mutation_fails_closed(tmp_path):
    path, _layout, _memory, runtime = _runtime(tmp_path)
    manager = runtime.memory_manager(path)
    session = runtime.open_spatial_session(path)

    session.values[MEMORY_ROOT_KEY]["policies"]["facts"]["max_bytes"] += 1

    with pytest.raises(ValueError, match="modified unexpectedly"):
        manager.usage("facts")

    runtime.close(flush=False)


def test_corrupt_sequence_metadata_fails_closed(tmp_path):
    path, _layout, _memory, runtime = _runtime(tmp_path)
    manager = runtime.memory_manager(path)
    manager.set("facts", "a", 1)
    session = runtime.open_spatial_session(path)
    root = session.values[MEMORY_ROOT_KEY]
    root["next_sequence"] = 1

    with pytest.raises(ValueError, match="next_sequence"):
        manager.snapshot("facts")

    runtime.close(flush=False)
