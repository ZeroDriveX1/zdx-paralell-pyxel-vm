"""Quota-aware logical memory namespaces for resident Pyxel agents."""

from __future__ import annotations

import copy
import re

from zdx_agent_abi import ROLE_PERSISTENT_MEMORY
from zdx_pixel_memory.spatial_store import encoded_value_size


_ROOT_KEY = "agent_memory_v1"
_VERSION = 1
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_EVICTION = frozenset({"reject", "fifo"})


class AgentMemoryManager:
    """Manage namespaced agent memory inside the resident persistent-memory map.

    Quotas use the exact deterministic spatial binary encoding size. Mutations
    are staged and validated before they touch the live session values.
    """

    def __init__(self, session):
        self.session = session
        if session.abi.region_for(ROLE_PERSISTENT_MEMORY) is None:
            raise ValueError("agent memory manager requires persistent_memory ABI binding")
        self._validate_root(session.values.get(_ROOT_KEY))

    @staticmethod
    def _validate_name(value: str, what: str) -> str:
        if not isinstance(value, str) or not _SAFE_NAME.fullmatch(value):
            raise ValueError(f"{what} must match {_SAFE_NAME.pattern}")
        return value

    @staticmethod
    def _validate_root(root) -> None:
        if root is None:
            return
        if not isinstance(root, dict):
            raise ValueError("agent memory root must be an object")
        unknown_root = set(root) - {"version", "namespaces"}
        if unknown_root:
            raise ValueError(
                f"unknown agent memory root fields: {sorted(unknown_root)}"
            )
        if root.get("version") != _VERSION:
            raise ValueError("unsupported agent memory schema version")
        namespaces = root.get("namespaces")
        if not isinstance(namespaces, dict):
            raise ValueError("agent memory namespaces must be an object")
        for name, record in namespaces.items():
            AgentMemoryManager._validate_name(name, "namespace")
            if not isinstance(record, dict):
                raise ValueError("agent memory namespace record must be an object")
            quota = record.get("quota_bytes")
            if isinstance(quota, bool) or not isinstance(quota, int) or quota < 1:
                raise ValueError("agent memory namespace quota must be positive")
            if record.get("eviction") not in _EVICTION:
                raise ValueError("unsupported agent memory eviction policy")
            allowed_fields = {"quota_bytes", "eviction", "items", "order"}
            unknown = set(record) - allowed_fields
            if unknown:
                raise ValueError(
                    f"unknown agent memory namespace fields: {sorted(unknown)}"
                )
            items = record.get("items")
            order = record.get("order")
            if not isinstance(items, dict) or not isinstance(order, list):
                raise ValueError("agent memory namespace items/order are malformed")
            for key in items:
                AgentMemoryManager._validate_name(key, "memory key")
            if any(not isinstance(key, str) for key in order):
                raise ValueError("agent memory namespace order keys must be strings")
            if len(set(order)) != len(order):
                raise ValueError("agent memory namespace order contains duplicates")
            if set(order) != set(items):
                raise ValueError(
                    "agent memory namespace order must exactly cover stored keys"
                )
            if AgentMemoryManager._namespace_used(record) > quota:
                raise ValueError("persisted agent memory namespace exceeds quota")

    def _root_copy(self) -> dict:
        root = self.session.values.get(_ROOT_KEY)
        if root is None:
            return {"version": _VERSION, "namespaces": {}}
        self._validate_root(root)
        return copy.deepcopy(root)

    def _commit_root(self, root: dict) -> None:
        candidate = copy.deepcopy(self.session.values)
        candidate[_ROOT_KEY] = root
        encoded = self.session.memory.encoded_size(candidate)
        capacity = self.session.memory.capacity_bytes
        if encoded is not None and capacity is not None and encoded > capacity:
            raise ValueError(
                f"agent memory document requires {encoded} bytes; "
                f"persistent region capacity is {capacity}"
            )
        self.session.values[_ROOT_KEY] = root
        self.session.mark_dirty(ROLE_PERSISTENT_MEMORY)

    @staticmethod
    def _namespace_used(record: dict) -> int:
        return encoded_value_size({
            "items": record["items"],
            "order": record["order"],
        })

    def configure(
        self,
        namespace: str,
        *,
        quota_bytes: int,
        eviction: str = "reject",
    ) -> dict:
        namespace = self._validate_name(namespace, "namespace")
        if isinstance(quota_bytes, bool) or not isinstance(quota_bytes, int) or quota_bytes < 1:
            raise ValueError("quota_bytes must be a positive integer")
        if eviction not in _EVICTION:
            raise ValueError(f"eviction must be one of {sorted(_EVICTION)}")
        capacity = self.session.memory.capacity_bytes
        if capacity is not None and quota_bytes > capacity:
            raise ValueError("namespace quota cannot exceed persistent region capacity")

        root = self._root_copy()
        existing = root["namespaces"].get(namespace)
        record = existing or {
            "quota_bytes": quota_bytes,
            "eviction": eviction,
            "items": {},
            "order": [],
        }
        record["quota_bytes"] = quota_bytes
        record["eviction"] = eviction
        if self._namespace_used(record) > quota_bytes:
            if eviction == "fifo":
                self._evict_to_quota(record)
            else:
                raise ValueError("existing namespace contents exceed requested quota")
        root["namespaces"][namespace] = record
        self._commit_root(root)
        self.session.record_event(
            "memory.configure",
            f"{namespace}:{quota_bytes}:{eviction}".encode("utf-8"),
        )
        return self.stats(namespace)

    def _namespace(self, root: dict, namespace: str) -> dict:
        namespace = self._validate_name(namespace, "namespace")
        try:
            return root["namespaces"][namespace]
        except KeyError as exc:
            raise KeyError(f"unconfigured agent memory namespace: {namespace}") from exc

    def _evict_to_quota(self, record: dict) -> list[str]:
        evicted: list[str] = []
        while self._namespace_used(record) > record["quota_bytes"] and record["order"]:
            victim = record["order"].pop(0)
            if victim in record["items"]:
                record["items"].pop(victim, None)
                evicted.append(victim)
        if self._namespace_used(record) > record["quota_bytes"]:
            raise ValueError("namespace metadata alone exceeds its quota")
        return evicted

    def put(self, namespace: str, key: str, value) -> dict:
        key = self._validate_name(key, "memory key")
        root = self._root_copy()
        record = self._namespace(root, namespace)
        old = copy.deepcopy(record)
        record["items"][key] = copy.deepcopy(value)
        record["order"] = [item for item in record["order"] if item != key]
        record["order"].append(key)

        evicted: list[str] = []
        if self._namespace_used(record) > record["quota_bytes"]:
            if record["eviction"] != "fifo":
                root["namespaces"][namespace] = old
                raise ValueError("agent memory namespace quota exceeded")
            evicted = self._evict_to_quota(record)
            if key not in record["items"]:
                root["namespaces"][namespace] = old
                raise ValueError("agent memory item cannot fit within namespace quota")

        self._commit_root(root)
        self.session.record_event(
            "memory.put",
            f"{namespace}:{key}:{len(evicted)}".encode("utf-8"),
        )
        return {"key": key, "evicted": evicted, **self.stats(namespace, root=root)}

    def get(self, namespace: str, key: str, default=None):
        key = self._validate_name(key, "memory key")
        root = self._root_copy()
        record = self._namespace(root, namespace)
        return copy.deepcopy(record["items"].get(key, default))

    def delete(self, namespace: str, key: str) -> bool:
        key = self._validate_name(key, "memory key")
        root = self._root_copy()
        record = self._namespace(root, namespace)
        if key not in record["items"]:
            return False
        record["items"].pop(key)
        record["order"] = [item for item in record["order"] if item != key]
        self._commit_root(root)
        self.session.record_event(
            "memory.delete", f"{namespace}:{key}".encode("utf-8")
        )
        return True

    def compact(self, namespace: str) -> dict:
        root = self._root_copy()
        record = self._namespace(root, namespace)
        seen = set()
        order = []
        for key in record["order"]:
            if key in record["items"] and key not in seen:
                seen.add(key)
                order.append(key)
        for key in sorted(record["items"]):
            if key not in seen:
                order.append(key)
        record["order"] = order
        if record["eviction"] == "fifo":
            self._evict_to_quota(record)
        elif self._namespace_used(record) > record["quota_bytes"]:
            raise ValueError("namespace remains over quota after compaction")
        self._commit_root(root)
        self.session.record_event(
            "memory.compact", namespace.encode("utf-8")
        )
        return self.stats(namespace, root=root)

    def clear(self, namespace: str) -> None:
        root = self._root_copy()
        record = self._namespace(root, namespace)
        record["items"] = {}
        record["order"] = []
        self._commit_root(root)
        self.session.record_event(
            "memory.clear", namespace.encode("utf-8")
        )

    def stats(self, namespace: str, *, root: dict | None = None) -> dict:
        source = self._root_copy() if root is None else root
        record = self._namespace(source, namespace)
        used = self._namespace_used(record)
        return {
            "namespace": namespace,
            "items": len(record["items"]),
            "used_bytes": used,
            "quota_bytes": record["quota_bytes"],
            "free_bytes": max(0, record["quota_bytes"] - used),
            "eviction": record["eviction"],
        }

    def namespaces(self) -> list[str]:
        return sorted(self._root_copy()["namespaces"])
