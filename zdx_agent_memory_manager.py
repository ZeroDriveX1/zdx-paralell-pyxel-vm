"""Deterministic namespaced memory manager for resident spatial agents."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Mapping


MEMORY_ROOT_KEY = "agent_memory_namespaces"
MEMORY_SCHEMA_VERSION = 1
_NAMESPACE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_KEY = re.compile(r"^[A-Za-z0-9_.:/-]{1,160}$")
_EVICTION = frozenset({"reject", "fifo"})


class MemoryQuotaExceeded(ValueError):
    pass


@dataclass(frozen=True)
class MemoryNamespacePolicy:
    name: str
    max_bytes: int
    max_entries: int
    eviction: str = "reject"

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _NAMESPACE.fullmatch(self.name):
            raise ValueError("memory namespace name is invalid")
        if isinstance(self.max_bytes, bool) or not isinstance(self.max_bytes, int) or self.max_bytes < 64:
            raise ValueError("memory namespace max_bytes must be an integer >= 64")
        if isinstance(self.max_entries, bool) or not isinstance(self.max_entries, int) or self.max_entries < 1:
            raise ValueError("memory namespace max_entries must be a positive integer")
        if self.eviction not in _EVICTION:
            raise ValueError(f"unsupported memory eviction policy: {self.eviction!r}")

    def to_dict(self) -> dict:
        return {
            "max_bytes": self.max_bytes,
            "max_entries": self.max_entries,
            "eviction": self.eviction,
        }

    @classmethod
    def from_dict(cls, name: str, payload: Mapping) -> "MemoryNamespacePolicy":
        if not isinstance(payload, Mapping):
            raise ValueError("memory namespace policy must be an object")
        allowed = {"max_bytes", "max_entries", "eviction"}
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(f"unknown memory namespace policy fields: {sorted(unknown)}")
        return cls(
            name=name,
            max_bytes=payload.get("max_bytes"),
            max_entries=payload.get("max_entries"),
            eviction=str(payload.get("eviction", "reject")),
        )


def default_memory_policies(capacity_bytes: int) -> tuple[MemoryNamespacePolicy, ...]:
    if isinstance(capacity_bytes, bool) or not isinstance(capacity_bytes, int) or capacity_bytes < 512:
        raise ValueError("persistent memory region is too small for namespaced memory")
    budget = max(320, int(capacity_bytes * 0.60))
    weights = {
        "working": (20, 128, "fifo"),
        "episodic": (25, 256, "fifo"),
        "facts": (25, 256, "reject"),
        "tool_results": (20, 128, "fifo"),
        "system": (10, 64, "reject"),
    }
    policies = []
    for name, (weight, entries, eviction) in weights.items():
        policies.append(
            MemoryNamespacePolicy(
                name=name,
                max_bytes=max(64, budget * weight // 100),
                max_entries=entries,
                eviction=eviction,
            )
        )
    return tuple(policies)


class SpatialAgentMemoryManager:
    """Namespaced/quota-aware memory over one session-owned persistent document.

    The manager never writes the PNG directly. It mutates the resident session
    document after validating both namespace quota and full-document encoded
    capacity. Durability is owned by SpatialAgentSession checkpoints.
    """

    def __init__(
        self,
        session,
        policies: Mapping[str, MemoryNamespacePolicy | Mapping] | None = None,
    ):
        self.session = session
        self._store = getattr(getattr(session, "memory", None), "_store", None)
        if self._store is None or not callable(getattr(self._store, "estimate_document_size", None)):
            raise RuntimeError("session memory backend lacks deterministic size estimation")

        root = session.values.get(MEMORY_ROOT_KEY)
        if root is None:
            selected = self._normalize_policies(policies) if policies is not None else {
                p.name: p for p in default_memory_policies(self._store.capacity_bytes)
            }
            root = {
                "version": MEMORY_SCHEMA_VERSION,
                "next_sequence": 1,
                "policies": {name: policy.to_dict() for name, policy in sorted(selected.items())},
                "namespaces": {name: {"entries": {}} for name in sorted(selected)},
            }
            candidate = copy.deepcopy(session.values)
            candidate[MEMORY_ROOT_KEY] = root
            self._ensure_document_capacity(candidate)
            session.values[MEMORY_ROOT_KEY] = root
            session.mark_dirty()
            self._policies = selected
        else:
            self._validate_root(root)
            persisted = {
                name: MemoryNamespacePolicy.from_dict(name, raw)
                for name, raw in root["policies"].items()
            }
            if policies is not None:
                requested = self._normalize_policies(policies)
                if self._policy_payload(requested) != self._policy_payload(persisted):
                    raise ValueError("persisted memory namespace policies do not match requested policies")
            self._policies = persisted

    @staticmethod
    def _policy_payload(policies: Mapping[str, MemoryNamespacePolicy]) -> dict:
        return {name: policy.to_dict() for name, policy in sorted(policies.items())}

    @staticmethod
    def _normalize_policies(
        policies: Mapping[str, MemoryNamespacePolicy | Mapping],
    ) -> dict[str, MemoryNamespacePolicy]:
        if not isinstance(policies, Mapping) or not policies:
            raise ValueError("memory policies must be a non-empty mapping")
        normalized: dict[str, MemoryNamespacePolicy] = {}
        for name, raw in policies.items():
            if isinstance(raw, MemoryNamespacePolicy):
                policy = raw
                if policy.name != name:
                    raise ValueError("memory namespace policy name does not match mapping key")
            else:
                policy = MemoryNamespacePolicy.from_dict(str(name), raw)
            if policy.name in normalized:
                raise ValueError(f"duplicate memory namespace: {policy.name}")
            normalized[policy.name] = policy
        return normalized

    def _validate_root(self, root) -> None:
        if not isinstance(root, dict):
            raise ValueError("persisted namespaced memory root must be an object")
        allowed = {"version", "next_sequence", "policies", "namespaces"}
        unknown = set(root) - allowed
        if unknown:
            raise ValueError(f"unknown namespaced memory root fields: {sorted(unknown)}")
        if root.get("version") != MEMORY_SCHEMA_VERSION:
            raise ValueError("unsupported namespaced memory schema version")
        next_sequence = root.get("next_sequence")
        if isinstance(next_sequence, bool) or not isinstance(next_sequence, int) or next_sequence < 1:
            raise ValueError("namespaced memory next_sequence is invalid")
        policies = root.get("policies")
        namespaces = root.get("namespaces")
        if not isinstance(policies, dict) or not isinstance(namespaces, dict):
            raise ValueError("namespaced memory policies/namespaces must be objects")
        if set(policies) != set(namespaces):
            raise ValueError("namespaced memory policy and namespace sets differ")
        seen_sequences = set()
        max_sequence = 0
        for name, policy_raw in policies.items():
            MemoryNamespacePolicy.from_dict(name, policy_raw)
            bucket = namespaces[name]
            if not isinstance(bucket, dict) or set(bucket) != {"entries"} or not isinstance(bucket["entries"], dict):
                raise ValueError(f"memory namespace {name!r} has invalid structure")
            for key, record in bucket["entries"].items():
                self._validate_key(key)
                if not isinstance(record, dict) or set(record) != {"sequence", "value"}:
                    raise ValueError(f"memory entry {name}/{key} has invalid structure")
                sequence = record["sequence"]
                if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
                    raise ValueError(f"memory entry {name}/{key} has invalid sequence")
                if sequence in seen_sequences:
                    raise ValueError("namespaced memory contains duplicate entry sequences")
                seen_sequences.add(sequence)
                max_sequence = max(max_sequence, sequence)
        if max_sequence >= next_sequence:
            raise ValueError("namespaced memory next_sequence does not follow persisted entries")

    @staticmethod
    def _validate_key(key: str) -> str:
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise ValueError("memory key contains unsupported characters or length")
        return key

    def _root(self) -> dict:
        root = self.session.values.get(MEMORY_ROOT_KEY)
        self._validate_root(root)
        return root

    def _namespace_encoded_size(self, root: dict, namespace: str) -> int:
        return self._store.estimate_value_size(root["namespaces"][namespace])

    def _ensure_document_capacity(self, values: Mapping) -> None:
        required = self._store.estimate_document_size(values)
        if required > self._store.capacity_bytes:
            raise MemoryQuotaExceeded(
                f"agent memory document requires {required} bytes; "
                f"persistent region capacity is {self._store.capacity_bytes}"
            )

    def _oldest_key(self, entries: dict, *, exclude: str | None = None) -> str | None:
        candidates = [
            (record["sequence"], key)
            for key, record in entries.items()
            if key != exclude
        ]
        return min(candidates)[1] if candidates else None

    def _enforce_namespace(
        self,
        root: dict,
        namespace: str,
        *,
        protected_key: str | None = None,
    ) -> None:
        policy = self._policies[namespace]
        entries = root["namespaces"][namespace]["entries"]
        while (
            len(entries) > policy.max_entries
            or self._namespace_encoded_size(root, namespace) > policy.max_bytes
        ):
            if policy.eviction == "reject":
                raise MemoryQuotaExceeded(f"memory namespace {namespace!r} quota exceeded")
            victim = self._oldest_key(entries, exclude=protected_key)
            if victim is None:
                raise MemoryQuotaExceeded(
                    f"memory value for {namespace!r} cannot fit within its quota"
                )
            del entries[victim]

    def _ensure_global_capacity_with_fifo(
        self,
        candidate_values: dict,
        namespace: str,
        *,
        protected_key: str | None = None,
    ) -> None:
        policy = self._policies[namespace]
        root = candidate_values[MEMORY_ROOT_KEY]
        entries = root["namespaces"][namespace]["entries"]
        while self._store.estimate_document_size(candidate_values) > self._store.capacity_bytes:
            if policy.eviction != "fifo":
                self._ensure_document_capacity(candidate_values)
            victim = self._oldest_key(entries, exclude=protected_key)
            if victim is None:
                self._ensure_document_capacity(candidate_values)
            del entries[victim]

    def policies(self) -> dict:
        return self._policy_payload(self._policies)

    def namespaces(self) -> list[str]:
        return sorted(self._policies)

    def usage(self, namespace: str | None = None) -> dict:
        with self.session.locked():
            root = self._root()
            names = [namespace] if namespace is not None else self.namespaces()
            result = {}
            for name in names:
                self._require_namespace(name)
                entries = root["namespaces"][name]["entries"]
                policy = self._policies[name]
                result[name] = {
                    "entries": len(entries),
                    "encoded_bytes": self._namespace_encoded_size(root, name),
                    "max_entries": policy.max_entries,
                    "max_bytes": policy.max_bytes,
                    "eviction": policy.eviction,
                }
            return result

    def _require_namespace(self, namespace: str) -> MemoryNamespacePolicy:
        if namespace not in self._policies:
            raise KeyError(f"unknown memory namespace: {namespace}")
        return self._policies[namespace]

    def get(self, namespace: str, key: str, default=None):
        self._require_namespace(namespace)
        self._validate_key(key)
        with self.session.locked():
            record = self._root()["namespaces"][namespace]["entries"].get(key)
            return copy.deepcopy(record["value"]) if record is not None else default

    def keys(self, namespace: str) -> list[str]:
        self._require_namespace(namespace)
        with self.session.locked():
            return sorted(self._root()["namespaces"][namespace]["entries"])

    def snapshot(self, namespace: str) -> dict:
        self._require_namespace(namespace)
        with self.session.locked():
            entries = self._root()["namespaces"][namespace]["entries"]
            return {
                key: copy.deepcopy(record["value"])
                for key, record in sorted(entries.items())
            }

    def set(self, namespace: str, key: str, value) -> None:
        self._require_namespace(namespace)
        self._validate_key(key)
        with self.session.locked():
            candidate_values = copy.deepcopy(self.session.values)
            root = candidate_values[MEMORY_ROOT_KEY]
            sequence = root["next_sequence"]
            root["next_sequence"] = sequence + 1
            root["namespaces"][namespace]["entries"][key] = {
                "sequence": sequence,
                "value": copy.deepcopy(value),
            }
            self._enforce_namespace(root, namespace, protected_key=key)
            self._ensure_global_capacity_with_fifo(
                candidate_values, namespace, protected_key=key
            )
            self.session.values.clear()
            self.session.values.update(candidate_values)
            self.session.mark_dirty()

    def append(self, namespace: str, value) -> str:
        with self.session.locked():
            root = self._root()
            key = f"{root['next_sequence']:016x}"
            self.set(namespace, key, value)
            return key

    def delete(self, namespace: str, key: str) -> bool:
        self._require_namespace(namespace)
        self._validate_key(key)
        with self.session.locked():
            root = self._root()
            entries = root["namespaces"][namespace]["entries"]
            if key not in entries:
                return False
            candidate_values = copy.deepcopy(self.session.values)
            del candidate_values[MEMORY_ROOT_KEY]["namespaces"][namespace]["entries"][key]
            self.session.values.clear()
            self.session.values.update(candidate_values)
            self.session.mark_dirty()
            return True

    def clear(self, namespace: str) -> None:
        self._require_namespace(namespace)
        with self.session.locked():
            candidate_values = copy.deepcopy(self.session.values)
            candidate_values[MEMORY_ROOT_KEY]["namespaces"][namespace]["entries"] = {}
            self.session.values.clear()
            self.session.values.update(candidate_values)
            self.session.mark_dirty()
