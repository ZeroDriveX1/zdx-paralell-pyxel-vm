import os

import pytest

from pyxel_registry import PyxelRegistry
from zdx_agent_abi import (
    ROLE_MAILBOX_IN,
    ROLE_MAILBOX_OUT,
    ROLE_PERSISTENT_MEMORY,
    SpatialAgentABI,
)
from zdx_agent_runtime import ZDXAgentRuntime
from zdx_pixel_memory import ZDXAgentMemory
from zdx_spatial_frame import SpatialCompiler, SpatialFrame, SpatialLayout, SpatialPyxelVM, SpatialRegion
from zdx_spatial_mailbox import SpatialMailbox


def _agent_layout():
    return SpatialLayout(
        width=64,
        height=40,
        execution_rows=1,
        regions=(
            SpatialRegion("memory", 0, 1, 64, 24),
            SpatialRegion("mailbox_in", 0, 25, 64, 7),
            SpatialRegion("mailbox_out", 0, 32, 64, 7),
        ),
    )


def test_agent_abi_v1_binds_only_distinct_nonexecuting_regions():
    layout = _agent_layout()
    abi = SpatialAgentABI.infer(layout, persistent_region="memory")

    assert abi.version == 1
    assert abi.require_region(ROLE_PERSISTENT_MEMORY) == "memory"
    assert abi.require_region(ROLE_MAILBOX_IN) == "mailbox_in"
    assert abi.require_region(ROLE_MAILBOX_OUT) == "mailbox_out"
    assert SpatialAgentABI.from_dict(abi.to_dict(), layout) == abi

    with pytest.raises(ValueError, match="multiple roles"):
        SpatialAgentABI.create(
            layout,
            {
                ROLE_PERSISTENT_MEMORY: "memory",
                ROLE_MAILBOX_IN: "mailbox_in",
                ROLE_MAILBOX_OUT: "mailbox_in",
            },
        )

    tampered = abi.to_dict()
    tampered["layout_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="layout hash"):
        SpatialAgentABI.from_dict(tampered, layout)


def test_native_spatial_mailbox_fifo_full_and_integrity():
    layout = _agent_layout()
    frame = SpatialFrame.blank(layout)
    box = SpatialMailbox(frame, "mailbox_in", slot_size=256)

    first = box.enqueue(b"first", sender="agent-a", recipient="agent-b", topic="task")
    second = box.enqueue(b"second", sender="agent-a", recipient="agent-b", topic="task")
    assert first.sequence == 1
    assert second.sequence == 2
    assert box.pending_count() == 2
    assert box.peek().payload == b"first"
    assert box.dequeue().payload == b"first"
    assert box.dequeue().payload == b"second"
    assert box.dequeue() is None

    for index in range(box.slot_count):
        box.enqueue(str(index).encode(), sender="a", recipient="b")
    with pytest.raises(BufferError, match="full"):
        box.enqueue(b"overflow", sender="a", recipient="b")


def test_native_spatial_mailbox_detects_payload_tampering():
    layout = _agent_layout()
    frame = SpatialFrame.blank(layout)
    box = SpatialMailbox(frame, "mailbox_out", slot_size=256)
    box.enqueue(b"integrity-payload", sender="agent-a", recipient="agent-b")

    raw = bytearray(frame.read_bytes(layout.region("mailbox_out").capacity_bytes, region="mailbox_out"))
    offset = raw.find(b"integrity-payload")
    assert offset > 0
    raw[offset] ^= 0x01
    frame.write_bytes(raw, region="mailbox_out")

    with pytest.raises(ValueError, match="digest mismatch"):
        box.peek()


def test_spatial_agent_session_persists_abi_mailbox_and_vm_state(tmp_path):
    layout = _agent_layout()
    path = str(tmp_path / "agent.png")
    SpatialCompiler(layout).compile(
        [["SET_A 4", "SET_B 5", "ADD", "COPY_OUT", "STORE_MEM 0", "HALT"]],
        path,
    )

    memory = ZDXAgentMemory(
        agent_id="session",
        spatial=True,
        spatial_path=path,
        spatial_layout=layout,
        spatial_region="memory",
    )
    registry = PyxelRegistry()
    vm = SpatialPyxelVM(layout=layout)
    registry.register("vm", vm)
    registry.register("memory", memory)
    runtime = ZDXAgentRuntime(registry, checkpoint_interval=10)

    session = runtime.open_spatial_session(path)
    sent = session.send(
        b"route-this",
        sender="planner",
        recipient="worker",
        topic="dispatch",
    )
    assert sent.sequence == 1
    result = runtime.run_spatial(path)
    assert result["T0"]["OUT"] == 9
    assert session.generation == 1
    runtime.checkpoint(path, barrier=True)
    runtime.close()

    persisted = memory.snapshot()
    assert persisted["agent_abi"]["version"] == 1
    assert persisted["vm_checkpoint"]["generation"] == 1

    restarted_memory = ZDXAgentMemory(
        agent_id="session-restart",
        spatial=True,
        spatial_path=path,
        spatial_layout=layout,
        spatial_region="memory",
    )
    restarted_registry = PyxelRegistry()
    restarted_vm = SpatialPyxelVM(layout=layout)
    restarted_registry.register("vm", restarted_vm)
    restarted_registry.register("memory", restarted_memory)
    restarted = ZDXAgentRuntime(restarted_registry)
    restored_session = restarted.open_spatial_session(path)

    queued = restored_session.mailbox(ROLE_MAILBOX_OUT).peek()
    assert queued.payload == b"route-this"
    assert queued.sender == "planner"
    assert queued.recipient == "worker"
    assert restarted_vm.execution_generation == 1

    restarted.run_spatial(path)
    assert restarted_vm.execution_generation == 2
    restarted.close()


def test_persisted_agent_abi_cannot_be_silently_rebound(tmp_path):
    layout = _agent_layout()
    path = str(tmp_path / "abi.png")
    SpatialCompiler(layout).compile([["HALT"]], path)

    memory = ZDXAgentMemory(
        agent_id="abi",
        spatial=True,
        spatial_path=path,
        spatial_layout=layout,
        spatial_region="memory",
    )
    registry = PyxelRegistry()
    registry.register("vm", SpatialPyxelVM(layout=layout))
    registry.register("memory", memory)
    runtime = ZDXAgentRuntime(registry)
    runtime.open_spatial_session(path)
    runtime.checkpoint(path, barrier=True)
    runtime.close()

    changed = SpatialAgentABI.create(
        layout,
        {
            ROLE_PERSISTENT_MEMORY: "memory",
            ROLE_MAILBOX_IN: "mailbox_out",
            ROLE_MAILBOX_OUT: "mailbox_in",
        },
    )
    registry2 = PyxelRegistry()
    registry2.register("vm", SpatialPyxelVM(layout=layout))
    registry2.register(
        "memory",
        ZDXAgentMemory(
            agent_id="abi2",
            spatial=True,
            spatial_path=path,
            spatial_layout=layout,
            spatial_region="memory",
        ),
    )
    runtime2 = ZDXAgentRuntime(registry2)
    with pytest.raises(ValueError, match="persisted agent ABI"):
        runtime2.open_spatial_session(path, abi=changed)
    runtime2.close()


def test_clean_close_flushes_generation_before_interval(tmp_path):
    layout = SpatialLayout(
        width=48,
        height=24,
        execution_rows=1,
        regions=(SpatialRegion("memory", 0, 1, 48, 23),),
    )
    path = str(tmp_path / "close-flush.png")
    SpatialCompiler(layout).compile([["SET_A 7", "HALT"]], path)
    memory = ZDXAgentMemory(
        agent_id="close-flush",
        spatial=True,
        spatial_path=path,
        spatial_layout=layout,
        spatial_region="memory",
    )
    registry = PyxelRegistry()
    vm = SpatialPyxelVM(layout=layout)
    registry.register("vm", vm)
    registry.register("memory", memory)
    runtime = ZDXAgentRuntime(registry, checkpoint_interval=10)

    for _ in range(3):
        runtime.run_spatial(path)
    assert vm.execution_generation == 3
    runtime.close(flush=True)

    reopened = ZDXAgentMemory(
        agent_id="close-flush-reopen",
        spatial=True,
        spatial_path=path,
        spatial_layout=layout,
        spatial_region="memory",
    )
    assert reopened.snapshot()["vm_checkpoint"]["generation"] == 3
