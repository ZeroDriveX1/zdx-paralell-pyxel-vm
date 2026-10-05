"""Fail-closed action/tool authorization for Pyxel-native spatial agents."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Callable, Mapping


TOOL_POLICY_VERSION = 1
ACTION_DOMAIN = "zdx-agent-action-v1"
POLICY_DOMAIN = b"zdx-agent-tool-policy-v1\x00"
MAX_ACTION_BYTES = 64 * 1024
_DECISIONS = frozenset({"allow", "deny", "approval_required"})
_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class ToolAuthorizationError(PermissionError):
    pass


class AmbiguousToolOutcomeError(RuntimeError):
    pass


class ToolExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolCapabilityRule:
    tool: str
    operations: tuple[str, ...]
    resource_prefixes: tuple[str, ...] = ()
    require_barrier: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.tool, str) or not _NAME.fullmatch(self.tool):
            raise ValueError("tool capability name is invalid")
        if not self.operations or any(
            not isinstance(op, str) or not _NAME.fullmatch(op)
            for op in self.operations
        ):
            raise ValueError("tool capability operations must be non-empty exact names")
        if len(set(self.operations)) != len(self.operations):
            raise ValueError("tool capability operations contain duplicates")
        if any(
            not isinstance(prefix, str) or not prefix or len(prefix) > 512
            for prefix in self.resource_prefixes
        ):
            raise ValueError("tool capability resource prefixes must be non-empty strings")
        if not isinstance(self.require_barrier, bool):
            raise ValueError("require_barrier must be boolean")

    def to_dict(self) -> dict:
        return {
            "tool": self.tool,
            "operations": sorted(self.operations),
            "resource_prefixes": sorted(self.resource_prefixes),
            "require_barrier": self.require_barrier,
        }

    @classmethod
    def from_dict(cls, payload: Mapping) -> "ToolCapabilityRule":
        if not isinstance(payload, Mapping):
            raise ValueError("tool capability rule must be an object")
        allowed = {"tool", "operations", "resource_prefixes", "require_barrier"}
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(f"unknown tool capability fields: {sorted(unknown)}")
        operations = payload.get("operations")
        prefixes = payload.get("resource_prefixes", [])
        if not isinstance(operations, list) or not isinstance(prefixes, list):
            raise ValueError("tool capability operations/resource_prefixes must be arrays")
        return cls(
            tool=str(payload.get("tool", "")),
            operations=tuple(operations),
            resource_prefixes=tuple(prefixes),
            require_barrier=payload.get("require_barrier", True),
        )


@dataclass(frozen=True)
class AgentToolPolicy:
    policy_id: str
    rules: tuple[ToolCapabilityRule, ...]
    version: int = TOOL_POLICY_VERSION

    def __post_init__(self) -> None:
        if self.version != TOOL_POLICY_VERSION:
            raise ValueError(
                f"unsupported tool policy version {self.version}; expected {TOOL_POLICY_VERSION}"
            )
        if not isinstance(self.policy_id, str) or not _NAME.fullmatch(self.policy_id):
            raise ValueError("tool policy_id is invalid")
        tools = [rule.tool for rule in self.rules]
        if len(set(tools)) != len(tools):
            raise ValueError("tool policy contains duplicate tool rules")

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "policy_id": self.policy_id,
            "rules": [rule.to_dict() for rule in sorted(self.rules, key=lambda item: item.tool)],
        }

    @property
    def policy_hash(self) -> str:
        payload = json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        return hashlib.sha256(POLICY_DOMAIN + payload).hexdigest()

    @classmethod
    def from_dict(cls, payload: Mapping) -> "AgentToolPolicy":
        if not isinstance(payload, Mapping):
            raise ValueError("tool policy must be an object")
        allowed = {"version", "policy_id", "rules"}
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(f"unknown tool policy fields: {sorted(unknown)}")
        rules = payload.get("rules")
        if not isinstance(rules, list):
            raise ValueError("tool policy rules must be an array")
        return cls(
            version=payload.get("version"),
            policy_id=str(payload.get("policy_id", "")),
            rules=tuple(ToolCapabilityRule.from_dict(item) for item in rules),
        )

    def rule_for(self, tool: str) -> ToolCapabilityRule | None:
        for rule in self.rules:
            if rule.tool == tool:
                return rule
        return None


@dataclass(frozen=True)
class AgentActionProposal:
    tool: str
    operation: str
    resource: str
    arguments: dict
    idempotency_key: str
    generation: int
    checkpoint_hash: str
    policy_hash: str
    action_hash: str

    def to_dict(self) -> dict:
        return {
            "tool": self.tool,
            "operation": self.operation,
            "resource": self.resource,
            "arguments": copy.deepcopy(self.arguments),
            "idempotency_key": self.idempotency_key,
            "generation": self.generation,
            "checkpoint_hash": self.checkpoint_hash,
            "policy_hash": self.policy_hash,
            "action_hash": self.action_hash,
        }


@dataclass(frozen=True)
class AgentActionDecision:
    decision: str
    reason: str
    action_hash: str
    policy_hash: str

    def __post_init__(self) -> None:
        if self.decision not in _DECISIONS:
            raise ValueError("invalid tool authorization decision")

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"


class ToolRegistry:
    """Exact-name registry for tool handlers. No shell/path fallback exists."""

    def __init__(self):
        self._handlers: dict[str, Callable] = {}

    def register(self, tool: str, handler: Callable) -> None:
        if not isinstance(tool, str) or not _NAME.fullmatch(tool):
            raise ValueError("tool registration name is invalid")
        if not callable(handler):
            raise TypeError("tool handler must be callable")
        if tool in self._handlers:
            raise ValueError(f"tool already registered: {tool}")
        self._handlers[tool] = handler

    def get(self, tool: str) -> Callable:
        try:
            return self._handlers[tool]
        except KeyError as exc:
            raise ToolAuthorizationError(f"tool is not registered: {tool}") from exc

    def registered(self) -> list[str]:
        return sorted(self._handlers)


def _canonical_arguments(value):
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        raise TypeError(
            "tool action hashes do not accept floats; use integer units or canonical strings"
        )
    if isinstance(value, list):
        return [_canonical_arguments(item) for item in value]
    if isinstance(value, tuple):
        return [_canonical_arguments(item) for item in value]
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("tool argument object keys must be strings")
        return {
            key: _canonical_arguments(value[key])
            for key in sorted(value)
        }
    raise TypeError(f"unsupported tool argument type: {type(value).__name__}")


class AgentToolGateway:
    """Authorize and execute exact registered tools with durable intent/result state."""

    POLICY_MEMORY_KEY = "tool-policy"
    INTENT_PREFIX = "tool-intent/"
    RESULT_PREFIX = "tool-result/"

    def __init__(
        self,
        session,
        *,
        registry: ToolRegistry,
        policy: AgentToolPolicy,
        external_authorizer: Callable[[dict], object] | None = None,
    ):
        if not isinstance(registry, ToolRegistry):
            raise TypeError("registry must be a ToolRegistry")
        if not isinstance(policy, AgentToolPolicy):
            raise TypeError("policy must be an AgentToolPolicy")
        if external_authorizer is not None and not callable(external_authorizer):
            raise TypeError("external_authorizer must be callable")
        self.session = session
        self.registry = registry
        self.policy = policy
        self.external_authorizer = external_authorizer
        self.memory = session.memory_manager()
        required = {"system", "tool_results"}
        missing = required - set(self.memory.namespaces())
        if missing:
            raise RuntimeError(
                f"tool gateway requires memory namespaces: {sorted(missing)}"
            )
        persisted = self.memory.get("system", self.POLICY_MEMORY_KEY)
        policy_payload = {
            "policy": self.policy.to_dict(),
            "policy_hash": self.policy.policy_hash,
        }
        if persisted is None:
            self.memory.set("system", self.POLICY_MEMORY_KEY, policy_payload)
        elif persisted != policy_payload:
            raise ToolAuthorizationError(
                "persisted tool capability policy does not match requested policy"
            )

    @staticmethod
    def _proposal_payload(
        *,
        tool: str,
        operation: str,
        resource: str,
        arguments: dict,
        idempotency_key: str,
        generation: int,
        checkpoint_hash: str,
        policy_hash: str,
    ) -> dict:
        return {
            "domain": ACTION_DOMAIN,
            "tool": tool,
            "operation": operation,
            "resource": resource,
            "arguments": arguments,
            "idempotency_key": idempotency_key,
            "generation": generation,
            "checkpoint_hash": checkpoint_hash,
            "policy_hash": policy_hash,
        }

    def propose(
        self,
        *,
        tool: str,
        operation: str,
        resource: str = "",
        arguments: Mapping | None = None,
        idempotency_key: str = "default",
    ) -> AgentActionProposal:
        if not isinstance(tool, str) or not _NAME.fullmatch(tool):
            raise ValueError("tool name is invalid")
        if not isinstance(operation, str) or not _NAME.fullmatch(operation):
            raise ValueError("tool operation is invalid")
        if not isinstance(resource, str) or len(resource) > 2048:
            raise ValueError("tool resource is invalid")
        if not isinstance(idempotency_key, str) or not _NAME.fullmatch(idempotency_key):
            raise ValueError("tool idempotency_key is invalid")
        canonical_args = _canonical_arguments(dict(arguments or {}))
        payload = self._proposal_payload(
            tool=tool,
            operation=operation,
            resource=resource,
            arguments=canonical_args,
            idempotency_key=idempotency_key,
            generation=self.session.generation,
            checkpoint_hash=self.session.checkpoint_hash,
            policy_hash=self.policy.policy_hash,
        )
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        if len(encoded) > MAX_ACTION_BYTES:
            raise ValueError("canonical tool action exceeds maximum size")
        action_hash = hashlib.sha256(
            b"zdx-agent-action-v1\x00" + encoded
        ).hexdigest()
        return AgentActionProposal(
            tool=tool,
            operation=operation,
            resource=resource,
            arguments=canonical_args,
            idempotency_key=idempotency_key,
            generation=payload["generation"],
            checkpoint_hash=payload["checkpoint_hash"],
            policy_hash=payload["policy_hash"],
            action_hash=action_hash,
        )

    def _local_decision(self, proposal: AgentActionProposal) -> AgentActionDecision:
        rule = self.policy.rule_for(proposal.tool)
        if rule is None:
            return AgentActionDecision(
                "deny", "tool is not granted by local policy",
                proposal.action_hash, proposal.policy_hash,
            )
        if proposal.operation not in rule.operations:
            return AgentActionDecision(
                "deny", "operation is not granted by local policy",
                proposal.action_hash, proposal.policy_hash,
            )
        if rule.resource_prefixes:
            if not any(proposal.resource.startswith(prefix) for prefix in rule.resource_prefixes):
                return AgentActionDecision(
                    "deny", "resource is outside granted prefixes",
                    proposal.action_hash, proposal.policy_hash,
                )
        elif proposal.resource:
            return AgentActionDecision(
                "deny", "policy grants no resource-bearing action for this tool",
                proposal.action_hash, proposal.policy_hash,
            )
        return AgentActionDecision(
            "allow", "local capability rule matched",
            proposal.action_hash, proposal.policy_hash,
        )

    @staticmethod
    def _normalize_external_decision(
        response,
        proposal: AgentActionProposal,
    ) -> AgentActionDecision:
        if not isinstance(response, Mapping):
            return AgentActionDecision(
                "deny", "external authorizer must return a bound decision object",
                proposal.action_hash, proposal.policy_hash,
            )
        decision = response.get("decision")
        reason = str(response.get("reason", "external authorizer decision"))
        if decision not in _DECISIONS:
            return AgentActionDecision(
                "deny", "external authorizer returned an unknown decision",
                proposal.action_hash, proposal.policy_hash,
            )
        if response.get("action_hash") != proposal.action_hash:
            return AgentActionDecision(
                "deny", "external authorization action hash does not match proposal",
                proposal.action_hash, proposal.policy_hash,
            )
        if response.get("policy_hash") != proposal.policy_hash:
            return AgentActionDecision(
                "deny", "external authorization policy hash does not match proposal",
                proposal.action_hash, proposal.policy_hash,
            )
        return AgentActionDecision(
            decision, reason, proposal.action_hash, proposal.policy_hash
        )

    def authorize(
        self,
        proposal: AgentActionProposal,
        *,
        approval=None,
    ) -> AgentActionDecision:
        local = self._local_decision(proposal)
        if not local.allowed:
            return local
        if self.external_authorizer is None:
            return local
        external_payload = {
            "proposal": proposal.to_dict(),
            "approval": copy.deepcopy(approval),
        }
        try:
            response = self.external_authorizer(external_payload)
        except Exception as exc:
            return AgentActionDecision(
                "deny",
                f"external authorizer failed: {type(exc).__name__}",
                proposal.action_hash,
                proposal.policy_hash,
            )
        return self._normalize_external_decision(response, proposal)

    def _intent_key(self, action_hash: str) -> str:
        return self.INTENT_PREFIX + action_hash

    def _result_key(self, action_hash: str) -> str:
        return self.RESULT_PREFIX + action_hash

    def execute(
        self,
        *,
        tool: str,
        operation: str,
        resource: str = "",
        arguments: Mapping | None = None,
        idempotency_key: str = "default",
        approval=None,
    ):
        proposal = self.propose(
            tool=tool,
            operation=operation,
            resource=resource,
            arguments=arguments,
            idempotency_key=idempotency_key,
        )
        decision = self.authorize(proposal, approval=approval)
        if not decision.allowed:
            raise ToolAuthorizationError(
                f"{decision.decision}: {decision.reason}"
            )

        result_key = self._result_key(proposal.action_hash)
        existing_result = self.memory.get("tool_results", result_key)
        if existing_result is not None:
            return copy.deepcopy(existing_result["result"])

        intent_key = self._intent_key(proposal.action_hash)
        existing_intent = self.memory.get("system", intent_key)
        if existing_intent is not None:
            raise AmbiguousToolOutcomeError(
                "tool action already has a durable intent without a durable result"
            )

        rule = self.policy.rule_for(proposal.tool)
        handler = self.registry.get(proposal.tool)
        intent = {
            "status": "pending",
            "proposal": proposal.to_dict(),
            "decision": {
                "decision": decision.decision,
                "reason": decision.reason,
            },
        }
        self.memory.set("system", intent_key, intent)
        if rule.require_barrier:
            self.session.checkpoint(barrier=True)

        try:
            result = handler(proposal)
        except Exception as exc:
            failed = copy.deepcopy(intent)
            failed["status"] = "ambiguous"
            failed["error"] = {
                "type": type(exc).__name__,
                "message": str(exc)[:512],
            }
            try:
                self.memory.set("system", intent_key, failed)
                if rule.require_barrier:
                    self.session.checkpoint(barrier=True)
            except Exception as persist_exc:
                raise AmbiguousToolOutcomeError(
                    "tool failed and ambiguous-outcome state could not be durably recorded"
                ) from persist_exc
            raise ToolExecutionError(
                f"tool handler raised {type(exc).__name__}; outcome requires review"
            ) from exc

        result_record = {
            "action_hash": proposal.action_hash,
            "proposal": proposal.to_dict(),
            "result": copy.deepcopy(result),
        }
        try:
            self.memory.set("tool_results", result_key, result_record)
            completed = copy.deepcopy(intent)
            completed["status"] = "executed"
            completed["result_key"] = result_key
            self.memory.set("system", intent_key, completed)
            if rule.require_barrier:
                self.session.checkpoint(barrier=True)
        except Exception as exc:
            raise AmbiguousToolOutcomeError(
                "tool executed but its result could not be durably recorded"
            ) from exc
        return result
