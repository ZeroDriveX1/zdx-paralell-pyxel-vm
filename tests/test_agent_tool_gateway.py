import pytest

from pyxel_registry import PyxelRegistry
from zdx_agent_memory_manager import (
    MEMORY_ROOT_KEY,
    MemoryNamespacePolicy,
)
from zdx_agent_runtime import ZDXAgentRuntime
from zdx_agent_tool_gateway import (
    AgentToolPolicy,
    AmbiguousToolOutcomeError,
    ToolAuthorizationError,
    ToolCapabilityRule,
    ToolExecutionError,
    ToolRegistry,
)
from zdx_pixel_memory import ZDXAgentMemory
from zdx_spatial_frame import SpatialCompiler, SpatialLayout, SpatialPyxelVM, SpatialRegion


def _runtime(tmp_path):
    layout = SpatialLayout(
        width=128,
        height=96,
        execution_rows=1,
        regions=(SpatialRegion("memory", 0, 1, 128, 95),),
    )
    path = str(tmp_path / "tool-agent.png")
    SpatialCompiler(layout).compile([["HALT"]], path)
    memory = ZDXAgentMemory(
        agent_id="tool-agent",
        spatial=True,
        spatial_path=path,
        spatial_layout=layout,
        spatial_region="memory",
    )
    registry = PyxelRegistry()
    registry.register("vm", SpatialPyxelVM(layout=layout))
    registry.register("memory", memory)
    runtime = ZDXAgentRuntime(registry, checkpoint_interval=10)
    return path, layout, memory, runtime


def _policy(*, operations=("write",), prefixes=("store://safe/",), barrier=True):
    return AgentToolPolicy(
        policy_id="test-policy",
        rules=(
            ToolCapabilityRule(
                tool="store",
                operations=tuple(operations),
                resource_prefixes=tuple(prefixes),
                require_barrier=barrier,
            ),
        ),
    )


def _bound_external(decision="allow"):
    def authorizer(payload):
        proposal = payload["proposal"]
        return {
            "decision": decision,
            "reason": "test external policy",
            "action_hash": proposal["action_hash"],
            "policy_hash": proposal["policy_hash"],
        }
    return authorizer


def _persisted_namespace(memory, namespace):
    root = memory.snapshot()[MEMORY_ROOT_KEY]
    return {
        key: record["value"]
        for key, record in root["namespaces"][namespace]["entries"].items()
    }


def test_tool_gateway_persists_intent_before_handler_and_result_after(tmp_path):
    path, _layout, memory, runtime = _runtime(tmp_path)
    calls = []
    tools = ToolRegistry()

    def handler(proposal):
        persisted_system = _persisted_namespace(memory, "system")
        intent_key = f"tool-intent/{proposal.action_hash}"
        assert persisted_system[intent_key]["status"] == "pending"
        calls.append(proposal.action_hash)
        return {"ok": True, "resource": proposal.resource}

    tools.register("store", handler)
    gateway = runtime.tool_gateway(
        path, registry=tools, policy=_policy()
    )

    result = gateway.execute(
        tool="store",
        operation="write",
        resource="store://safe/item-1",
        arguments={"value": 7},
        idempotency_key="request-1",
    )

    assert result == {"ok": True, "resource": "store://safe/item-1"}
    assert len(calls) == 1

    persisted_system = _persisted_namespace(memory, "system")
    persisted_results = _persisted_namespace(memory, "tool_results")
    proposal = gateway.propose(
        tool="store",
        operation="write",
        resource="store://safe/item-1",
        arguments={"value": 7},
        idempotency_key="request-1",
    )
    assert persisted_system[f"tool-intent/{proposal.action_hash}"]["status"] == "executed"
    assert persisted_results[f"tool-result/{proposal.action_hash}"]["result"] == result
    runtime.close()


def test_same_idempotency_key_replays_result_without_reexecuting(tmp_path):
    path, _layout, _memory, runtime = _runtime(tmp_path)
    calls = []
    tools = ToolRegistry()

    def handler(proposal):
        calls.append(proposal.idempotency_key)
        return {"count": len(calls)}

    tools.register("store", handler)
    gateway = runtime.tool_gateway(path, registry=tools, policy=_policy())

    first = gateway.execute(
        tool="store", operation="write",
        resource="store://safe/a", arguments={"x": 1},
        idempotency_key="same",
    )
    second = gateway.execute(
        tool="store", operation="write",
        resource="store://safe/a", arguments={"x": 1},
        idempotency_key="same",
    )

    assert first == second == {"count": 1}
    assert calls == ["same"]
    runtime.close()


def test_different_idempotency_keys_allow_intentional_repeat(tmp_path):
    path, _layout, _memory, runtime = _runtime(tmp_path)
    calls = []
    tools = ToolRegistry()
    tools.register("store", lambda proposal: calls.append(proposal.action_hash) or {"ok": True})
    gateway = runtime.tool_gateway(path, registry=tools, policy=_policy())

    gateway.execute(
        tool="store", operation="write",
        resource="store://safe/a", arguments={"x": 1},
        idempotency_key="one",
    )
    gateway.execute(
        tool="store", operation="write",
        resource="store://safe/a", arguments={"x": 1},
        idempotency_key="two",
    )

    assert len(calls) == 2
    assert calls[0] != calls[1]
    runtime.close()


@pytest.mark.parametrize(
    "kwargs,match",
    [
        (
            dict(tool="unknown", operation="write", resource="store://safe/a"),
            "tool is not granted",
        ),
        (
            dict(tool="store", operation="delete", resource="store://safe/a"),
            "operation is not granted",
        ),
        (
            dict(tool="store", operation="write", resource="store://other/a"),
            "resource is outside",
        ),
    ],
)
def test_local_capability_policy_denies_before_handler(tmp_path, kwargs, match):
    path, _layout, _memory, runtime = _runtime(tmp_path)
    calls = []
    tools = ToolRegistry()
    tools.register("store", lambda proposal: calls.append(proposal) or {})
    gateway = runtime.tool_gateway(path, registry=tools, policy=_policy())

    with pytest.raises(ToolAuthorizationError, match=match):
        gateway.execute(**kwargs, idempotency_key="denied")

    assert calls == []
    runtime.close()


def test_resource_is_denied_when_rule_has_no_resource_scope(tmp_path):
    path, _layout, _memory, runtime = _runtime(tmp_path)
    tools = ToolRegistry()
    tools.register("store", lambda proposal: {})
    gateway = runtime.tool_gateway(
        path,
        registry=tools,
        policy=_policy(prefixes=()),
    )

    with pytest.raises(ToolAuthorizationError, match="no resource-bearing"):
        gateway.execute(
            tool="store", operation="write",
            resource="store://safe/a", idempotency_key="resource-denied",
        )
    runtime.close()


def test_unregistered_tool_fails_closed_even_if_policy_grants_it(tmp_path):
    path, _layout, _memory, runtime = _runtime(tmp_path)
    gateway = runtime.tool_gateway(
        path,
        registry=ToolRegistry(),
        policy=_policy(),
    )

    with pytest.raises(ToolAuthorizationError, match="not registered"):
        gateway.execute(
            tool="store", operation="write",
            resource="store://safe/a", idempotency_key="unregistered",
        )
    runtime.close()


def test_external_authorization_must_echo_action_and_policy_hashes(tmp_path):
    path, _layout, _memory, runtime = _runtime(tmp_path)
    calls = []
    tools = ToolRegistry()
    tools.register("store", lambda proposal: calls.append(proposal) or {"ok": True})

    def mismatched(payload):
        return {
            "decision": "allow",
            "reason": "stale decision",
            "action_hash": "0" * 64,
            "policy_hash": payload["proposal"]["policy_hash"],
        }

    gateway = runtime.tool_gateway(
        path, registry=tools, policy=_policy(),
        external_authorizer=mismatched,
    )
    with pytest.raises(ToolAuthorizationError, match="action hash"):
        gateway.execute(
            tool="store", operation="write",
            resource="store://safe/a", idempotency_key="external-stale",
        )
    assert calls == []
    runtime.close()


@pytest.mark.parametrize("decision", ["deny", "approval_required"])
def test_external_deny_or_approval_required_does_not_execute(tmp_path, decision):
    path, _layout, _memory, runtime = _runtime(tmp_path)
    calls = []
    tools = ToolRegistry()
    tools.register("store", lambda proposal: calls.append(proposal) or {})
    gateway = runtime.tool_gateway(
        path, registry=tools, policy=_policy(),
        external_authorizer=_bound_external(decision),
    )

    with pytest.raises(ToolAuthorizationError, match=decision):
        gateway.execute(
            tool="store", operation="write",
            resource="store://safe/a", idempotency_key=f"external-{decision}",
            approval={"approval_id": "test"},
        )
    assert calls == []
    runtime.close()


def test_bound_external_allow_executes(tmp_path):
    path, _layout, _memory, runtime = _runtime(tmp_path)
    tools = ToolRegistry()
    tools.register("store", lambda proposal: {"allowed": proposal.action_hash})
    gateway = runtime.tool_gateway(
        path, registry=tools, policy=_policy(),
        external_authorizer=_bound_external("allow"),
    )

    result = gateway.execute(
        tool="store", operation="write",
        resource="store://safe/a", idempotency_key="external-allow",
    )
    assert result["allowed"]
    runtime.close()


def test_handler_failure_leaves_durable_ambiguous_intent_and_retry_does_not_execute(tmp_path):
    path, _layout, memory, runtime = _runtime(tmp_path)
    calls = []
    tools = ToolRegistry()

    def handler(proposal):
        calls.append(proposal.action_hash)
        raise RuntimeError("unknown external outcome")

    tools.register("store", handler)
    gateway = runtime.tool_gateway(path, registry=tools, policy=_policy())

    with pytest.raises(ToolExecutionError, match="outcome requires review"):
        gateway.execute(
            tool="store", operation="write",
            resource="store://safe/a", arguments={"x": 1},
            idempotency_key="ambiguous",
        )

    proposal = gateway.propose(
        tool="store", operation="write",
        resource="store://safe/a", arguments={"x": 1},
        idempotency_key="ambiguous",
    )
    persisted_system = _persisted_namespace(memory, "system")
    assert persisted_system[f"tool-intent/{proposal.action_hash}"]["status"] == "ambiguous"

    with pytest.raises(AmbiguousToolOutcomeError, match="durable intent"):
        gateway.execute(
            tool="store", operation="write",
            resource="store://safe/a", arguments={"x": 1},
            idempotency_key="ambiguous",
        )
    assert len(calls) == 1
    runtime.close()


def test_persisted_policy_mismatch_fails_closed_after_restart(tmp_path):
    path, layout, _memory, runtime = _runtime(tmp_path)
    tools = ToolRegistry()
    tools.register("store", lambda proposal: {"ok": True})
    runtime.tool_gateway(path, registry=tools, policy=_policy())
    runtime.checkpoint(path, barrier=True)
    runtime.close()

    memory2 = ZDXAgentMemory(
        agent_id="tool-agent-restart",
        spatial=True,
        spatial_path=path,
        spatial_layout=layout,
        spatial_region="memory",
    )
    registry2 = PyxelRegistry()
    registry2.register("vm", SpatialPyxelVM(layout=layout))
    registry2.register("memory", memory2)
    runtime2 = ZDXAgentRuntime(registry2)
    changed = _policy(operations=("write", "delete"))

    with pytest.raises(ToolAuthorizationError, match="does not match"):
        runtime2.tool_gateway(path, registry=tools, policy=changed)
    runtime2.close()


def test_action_hash_rejects_float_arguments(tmp_path):
    path, _layout, _memory, runtime = _runtime(tmp_path)
    tools = ToolRegistry()
    tools.register("store", lambda proposal: {})
    gateway = runtime.tool_gateway(path, registry=tools, policy=_policy())

    with pytest.raises(TypeError, match="do not accept floats"):
        gateway.propose(
            tool="store", operation="write",
            resource="store://safe/a",
            arguments={"ratio": 0.5},
            idempotency_key="float",
        )
    runtime.close()


def test_gateway_rejects_evicting_safety_namespaces(tmp_path):
    path, _layout, _memory, runtime = _runtime(tmp_path)
    runtime.memory_manager(
        path,
        policies={
            "system": MemoryNamespacePolicy(
                "system", max_bytes=8192, max_entries=64, eviction="reject"
            ),
            "tool_results": MemoryNamespacePolicy(
                "tool_results", max_bytes=8192, max_entries=64, eviction="fifo"
            ),
        },
    )
    tools = ToolRegistry()
    tools.register("store", lambda proposal: {"ok": True})

    with pytest.raises(RuntimeError, match="must use reject eviction"):
        runtime.tool_gateway(path, registry=tools, policy=_policy())

    runtime.close()


def test_corrupt_cached_result_fails_closed_instead_of_reusing(tmp_path):
    path, _layout, _memory, runtime = _runtime(tmp_path)
    calls = []
    tools = ToolRegistry()
    tools.register("store", lambda proposal: calls.append(proposal) or {"ok": True})
    gateway = runtime.tool_gateway(path, registry=tools, policy=_policy())

    gateway.execute(
        tool="store", operation="write",
        resource="store://safe/a", arguments={"x": 1},
        idempotency_key="corrupt-result",
    )
    proposal = gateway.propose(
        tool="store", operation="write",
        resource="store://safe/a", arguments={"x": 1},
        idempotency_key="corrupt-result",
    )
    result_key = f"tool-result/{proposal.action_hash}"
    memory_manager = runtime.memory_manager(path)
    record = memory_manager.get("tool_results", result_key)
    record["proposal"]["resource"] = "store://safe/other"
    memory_manager.set("tool_results", result_key, record)

    with pytest.raises(AmbiguousToolOutcomeError, match="does not match"):
        gateway.execute(
            tool="store", operation="write",
            resource="store://safe/a", arguments={"x": 1},
            idempotency_key="corrupt-result",
        )

    assert len(calls) == 1
    runtime.close()


def test_external_authorizer_exception_fails_closed(tmp_path):
    path, _layout, _memory, runtime = _runtime(tmp_path)
    calls = []
    tools = ToolRegistry()
    tools.register("store", lambda proposal: calls.append(proposal) or {"ok": True})

    def broken(_payload):
        raise RuntimeError("authorizer unavailable")

    gateway = runtime.tool_gateway(
        path, registry=tools, policy=_policy(), external_authorizer=broken
    )
    with pytest.raises(ToolAuthorizationError, match="external authorizer failed"):
        gateway.execute(
            tool="store", operation="write",
            resource="store://safe/a", idempotency_key="auth-failure",
        )

    assert calls == []
    runtime.close()


def test_action_hash_is_stable_across_argument_key_order(tmp_path):
    path, _layout, _memory, runtime = _runtime(tmp_path)
    tools = ToolRegistry()
    tools.register("store", lambda proposal: {})
    gateway = runtime.tool_gateway(path, registry=tools, policy=_policy())

    left = gateway.propose(
        tool="store", operation="write", resource="store://safe/a",
        arguments={"b": 2, "a": {"y": 4, "x": 3}},
        idempotency_key="canonical",
    )
    right = gateway.propose(
        tool="store", operation="write", resource="store://safe/a",
        arguments={"a": {"x": 3, "y": 4}, "b": 2},
        idempotency_key="canonical",
    )

    assert left.action_hash == right.action_hash
    runtime.close()
