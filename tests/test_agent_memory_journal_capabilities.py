import pytest

from pyxel_registry import PyxelRegistry
from zdx_agent_abi import ROLE_PROVENANCE
from zdx_agent_capabilities import AgentCapabilityGateway, CapabilityGrant
from zdx_agent_runtime import ZDXAgentRuntime
from zdx_pixel_memory import ZDXAgentMemory
from zdx_spatial_frame import SpatialCompiler, SpatialFrame, SpatialLayout, SpatialPyxelVM, SpatialRegion


def _layout():
    return SpatialLayout(
        width=96,
        height=64,
        execution_rows=1,
        regions=(
            SpatialRegion("memory", 0, 1, 96, 30),
            SpatialRegion("working_memory", 0, 31, 96, 4),
            SpatialRegion("mailbox_in", 0, 35, 96, 5),
            SpatialRegion("mailbox_out", 0, 40, 96, 5),
            SpatialRegion("capabilities", 0, 45, 96, 5),
            SpatialRegion("provenance", 0, 50, 96, 14),
        ),
    )


def _session(tmp_path, *, checkpoint_interval=10):
    layout = _layout()
    path = str(tmp_path / "agent.png")
    SpatialCompiler(layout).compile(
        [["SET_A 2", "SET_B 3", "ADD", "COPY_OUT", "STORE_MEM 0", "HALT"]],
        path,
    )
    memory = ZDXAgentMemory(
        agent_id="agent",
        spatial=True,
        spatial_path=path,
        spatial_layout=layout,
        spatial_region="memory",
    )
    registry = PyxelRegistry()
    registry.register("vm", SpatialPyxelVM(layout=layout))
    registry.register("memory", memory)
    runtime = ZDXAgentRuntime(registry, checkpoint_interval=checkpoint_interval)
    return layout, path, memory, runtime, runtime.open_spatial_session(path)


def test_namespaced_memory_enforces_exact_quota_and_fifo_eviction(tmp_path):
    _layout_value, path, memory, runtime, session = _session(tmp_path)
    manager = session.memory_manager()
    manager.configure("working", quota_bytes=360, eviction="fifo")

    saw_eviction = False
    for index in range(8):
        result = manager.put("working", f"k{index}", "x" * 80)
        saw_eviction = saw_eviction or bool(result["evicted"])
    assert saw_eviction
    stats = manager.stats("working")
    assert stats["used_bytes"] <= stats["quota_bytes"]
    assert manager.get("working", "k7") == "x" * 80
    assert stats["items"] < 8
    memory_events = [record.event_type for record in session.journal().records()]
    assert "memory.configure" in memory_events
    assert "memory.put" in memory_events

    manager.configure("facts", quota_bytes=220, eviction="reject")
    with pytest.raises(ValueError, match="quota exceeded"):
        manager.put("facts", "too-large", "y" * 500)

    runtime.close(flush=True)

    reopened = ZDXAgentMemory(
        agent_id="agent2",
        spatial=True,
        spatial_path=path,
        spatial_layout=_layout_value,
        spatial_region="memory",
    )
    registry = PyxelRegistry()
    registry.register("vm", SpatialPyxelVM(layout=_layout_value))
    registry.register("memory", reopened)
    runtime2 = ZDXAgentRuntime(registry)
    manager2 = runtime2.open_spatial_session(path).memory_manager()
    assert manager2.get("working", "k7") == "x" * 80
    assert manager2.stats("working")["used_bytes"] <= 360
    runtime2.close()


def test_provenance_journal_wraps_and_detects_tampering(tmp_path):
    layout, _path, _memory, runtime, session = _session(tmp_path)
    journal = session.journal()
    for index in range(journal.slot_count + 4):
        journal.append(
            "test.event",
            f"payload-{index}".encode(),
            generation=index,
        )
        session.mark_dirty(ROLE_PROVENANCE)

    records = journal.records()
    assert len(records) == journal.slot_count
    assert records[-1].payload == f"payload-{journal.slot_count + 3}".encode()
    assert records[0].sequence == 5
    assert journal.verify() is True

    region = layout.region("provenance")
    raw = bytearray(session.frame.read_bytes(region.capacity_bytes, region="provenance"))
    needle = records[-1].payload
    offset = raw.find(needle)
    assert offset > 0
    raw[offset] ^= 0x01
    session.frame.write_bytes(raw, region="provenance")
    with pytest.raises(ValueError, match="digest mismatch"):
        journal.records()
    runtime.close(flush=False)


def test_session_automatically_journals_execution_and_mailbox_activity(tmp_path):
    _layout_value, path, _memory, runtime, session = _session(tmp_path)
    session.send(
        b"dispatch",
        sender="planner",
        recipient="worker",
        topic="task",
    )
    runtime.run_spatial(path)

    events = [record.event_type for record in session.journal().records()]
    assert "mailbox.send" in events
    assert "vm.execute" in events
    runtime.close()


def test_capability_gateway_is_default_deny_and_state_bound(tmp_path):
    _layout_value, path, _memory, runtime, session = _session(tmp_path)
    gateway = AgentCapabilityGateway([
        CapabilityGrant("filesystem", ("file.read",)),
        CapabilityGrant("network", ("http.get",), approval_required=True),
    ])
    manifest = session.install_capability_gateway(gateway)
    assert len(manifest) == 64
    assert gateway.verify_installed(session)

    denied = session.decide_action(
        capability="filesystem",
        action="file.delete",
        arguments={"path": "/tmp/x"},
    )
    assert denied.status == "deny"
    assert denied.allowed is False
    assert denied.envelope is None

    allowed = session.decide_action(
        capability="filesystem",
        action="file.read",
        arguments={"path": "/safe/input"},
    )
    assert allowed.status == "allow"
    assert allowed.allowed is True
    assert len(allowed.action_hash) == 64
    assert len(allowed.envelope.artifact_sha256) == 64
    assert allowed.envelope.capability_manifest_sha256 == gateway.manifest_sha256
    assert len(allowed.decision_artifact_sha256) == 64

    approval = session.decide_action(
        capability="network",
        action="http.get",
        arguments={"url": "https://example.invalid/data"},
    )
    assert approval.status == "approval_required"
    assert approval.allowed is False

    runtime.run_spatial(path)
    later = session.decide_action(
        capability="filesystem",
        action="file.read",
        arguments={"path": "/safe/input"},
    )
    assert later.action_hash != allowed.action_hash

    event_types = [record.event_type for record in session.journal().records()]
    assert "capability.allow" in event_types
    assert "capability.approval_required" in event_types
    runtime.close()


def test_capability_table_tamper_fails_closed(tmp_path):
    layout, _path, _memory, runtime, session = _session(tmp_path)
    gateway = AgentCapabilityGateway([
        CapabilityGrant("filesystem", ("file.read",)),
    ])
    session.install_capability_gateway(gateway)

    region = layout.region("capabilities")
    raw = bytearray(session.frame.read_bytes(region.capacity_bytes, region="capabilities"))
    index = next(i for i, value in enumerate(raw) if value != 0)
    raw[index] ^= 0x01
    session.frame.write_bytes(raw, region="capabilities")
    session.mark_dirty("capabilities")

    decision = session.decide_action(
        capability="filesystem",
        action="file.read",
        arguments={"path": "/safe/input"},
    )
    assert decision.status == "deny"
    assert "verification failed" in decision.reason
    runtime.close(flush=False)


def test_dirty_rectangle_tracking_is_conservative_and_cleared_on_checkpoint(tmp_path):
    layout, _path, _memory, runtime, session = _session(tmp_path)
    session.frame.clear_dirty()
    assert session.dirty_rectangles == ()

    session.frame.write_cell(10, 10, (1, 2, 3))
    session.frame.write_cell(11, 10, (4, 5, 6))
    bounds = session.frame.dirty_bounds
    assert (bounds.x, bounds.y, bounds.width, bounds.height) == (10, 10, 2, 1)

    session.send(b"dirty", sender="a", recipient="b")
    assert session.dirty_rectangles
    assert "mailbox_out" in session.dirty_roles
    runtime.checkpoint(session.image_path, barrier=True)
    assert session.dirty_rectangles == ()
    assert session.dirty_roles == ()
    runtime.close()


def test_approval_required_cannot_be_downgraded_by_generic_evaluator(tmp_path):
    _layout_value, _path, _memory, runtime, session = _session(tmp_path)
    gateway = AgentCapabilityGateway(
        [CapabilityGrant("network", ("http.get",), approval_required=True)],
        evaluator=lambda _envelope, _grant: True,
    )
    session.install_capability_gateway(gateway)
    decision = session.decide_action(
        capability="network",
        action="http.get",
        arguments={"url": "https://example.invalid"},
    )
    assert decision.status == "approval_required"
    assert decision.allowed is False
    runtime.close()


@pytest.mark.parametrize("root", [
    {
        "version": 1,
        "namespaces": {},
        "unexpected": True,
    },
    {
        "version": 1,
        "namespaces": {
            "working": {
                "quota_bytes": 1024,
                "eviction": "fifo",
                "items": {"a": 1},
                "order": [],
            }
        },
    },
    {
        "version": 1,
        "namespaces": {
            "working": {
                "quota_bytes": 1024,
                "eviction": "fifo",
                "items": {"a": 1},
                "order": ["a", "a"],
            }
        },
    },
    {
        "version": 1,
        "namespaces": {
            "working": {
                "quota_bytes": 1,
                "eviction": "reject",
                "items": {"a": "too large"},
                "order": ["a"],
            }
        },
    },
])
def test_namespaced_memory_rejects_malformed_persisted_roots(tmp_path, root):
    _layout_value, _path, _memory, runtime, session = _session(tmp_path)
    session.values["agent_memory_v1"] = root
    from zdx_agent_memory_manager import AgentMemoryManager

    with pytest.raises(ValueError):
        AgentMemoryManager(session)
    runtime.close(flush=False)


def test_capability_gateway_requires_abi_capability_region(tmp_path):
    layout = SpatialLayout(
        width=64,
        height=24,
        execution_rows=1,
        regions=(SpatialRegion("memory", 0, 1, 64, 23),),
    )
    path = str(tmp_path / "no-capability-region.png")
    SpatialCompiler(layout).compile([["HALT"]], path)
    memory = ZDXAgentMemory(
        agent_id="no-cap",
        spatial=True,
        spatial_path=path,
        spatial_layout=layout,
        spatial_region="memory",
    )
    registry = PyxelRegistry()
    registry.register("vm", SpatialPyxelVM(layout=layout))
    registry.register("memory", memory)
    runtime = ZDXAgentRuntime(registry)
    session = runtime.open_spatial_session(path)
    gateway = AgentCapabilityGateway([
        CapabilityGrant("filesystem", ("file.read",)),
    ])

    with pytest.raises(ValueError, match="capabilities region"):
        session.install_capability_gateway(gateway)
    runtime.close(flush=False)


def test_capability_grant_rejects_string_as_action_collection():
    with pytest.raises(TypeError, match="tuple/list"):
        CapabilityGrant("filesystem", "file.read")


def test_capability_gateway_requires_provenance_region(tmp_path):
    layout = SpatialLayout(
        width=64,
        height=32,
        execution_rows=1,
        regions=(
            SpatialRegion("memory", 0, 1, 64, 20),
            SpatialRegion("capabilities", 0, 21, 64, 11),
        ),
    )
    path = str(tmp_path / "no-provenance.png")
    SpatialCompiler(layout).compile([["HALT"]], path)
    memory = ZDXAgentMemory(
        agent_id="no-prov",
        spatial=True,
        spatial_path=path,
        spatial_layout=layout,
        spatial_region="memory",
    )
    registry = PyxelRegistry()
    registry.register("vm", SpatialPyxelVM(layout=layout))
    registry.register("memory", memory)
    runtime = ZDXAgentRuntime(registry)
    session = runtime.open_spatial_session(path)
    gateway = AgentCapabilityGateway([
        CapabilityGrant("filesystem", ("file.read",)),
    ])

    with pytest.raises(ValueError, match="provenance region"):
        session.install_capability_gateway(gateway)
    runtime.close(flush=False)


def test_corrupt_provenance_denies_capability_decision(tmp_path):
    layout, _path, _memory, runtime, session = _session(tmp_path)
    gateway = AgentCapabilityGateway([
        CapabilityGrant("filesystem", ("file.read",)),
    ])
    session.install_capability_gateway(gateway)
    session.record_event("test.marker", b"before-corruption")

    region = layout.region("provenance")
    raw = bytearray(session.frame.read_bytes(region.capacity_bytes, region="provenance"))
    needle = b"before-corruption"
    offset = raw.find(needle)
    assert offset > 0
    raw[offset] ^= 0x01
    session.frame.write_bytes(raw, region="provenance")
    session.mark_dirty(ROLE_PROVENANCE)

    decision = session.decide_action(
        capability="filesystem",
        action="file.read",
        arguments={"path": "/safe/input"},
    )
    assert decision.status == "deny"
    assert "provenance journal verification failed" in decision.reason
    runtime.close(flush=False)
