"""Karmic master runtime and mergeable durable cluster state.

The elected master is always obtained from ``ZDXKarmaSystem``.  This module
does not contain a preferred address, bootstrap coordinator, or static leader.
Every node persists the same signed-operation-shaped state and can rebuild the
queue after an election or restart by merging operations received from peers.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from zdx_karma_system import ZDXKarmaSystem


MAX_OPERATIONS = 200_000


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class Contribution:
    node_id: str
    workload_id: str
    cpu_time_seconds: float
    ram_time_mb_seconds: float
    task_class: str
    verification_result: str
    completed_work: int
    karma_impact: int
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


class ReplicatedClusterState:
    """Atomic, idempotently mergeable operation log plus materialized queue."""

    def __init__(self, path: str = ".zdx/cluster_state.json", cluster_id: str = "default"):
        self.path = Path(path)
        self.cluster_id = cluster_id
        self._lock = threading.RLock()
        self._state = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "cluster_id": self.cluster_id, "operations": {}}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("version") != 1 or data.get("cluster_id") != self.cluster_id:
            raise ValueError("invalid replicated cluster state")
        if not isinstance(data.get("operations"), dict) or len(data["operations"]) > MAX_OPERATIONS:
            raise ValueError("invalid replicated operation log")
        return data

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(self._state, stream, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
            self.path.chmod(0o600)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    @staticmethod
    def _operation_hash(operation: dict) -> str:
        unsigned = {key: value for key, value in operation.items() if key != "operation_hash"}
        return hashlib.sha256(_canonical(unsigned).encode("utf-8")).hexdigest()

    def append(self, kind: str, payload: dict, origin: str) -> str:
        if not kind or not isinstance(payload, dict) or not origin:
            raise ValueError("operation kind, payload, and origin are required")
        operation = {
            "operation_id": str(uuid.uuid4()), "kind": kind, "payload": payload,
            "origin": origin, "timestamp": time.time(),
        }
        operation["operation_hash"] = self._operation_hash(operation)
        with self._lock:
            if len(self._state["operations"]) >= MAX_OPERATIONS:
                raise RuntimeError("replicated state operation limit reached")
            self._state["operations"][operation["operation_id"]] = operation
            self._save()
        return operation["operation_id"]

    def merge(self, snapshot: dict) -> int:
        if snapshot.get("version") != 1 or snapshot.get("cluster_id") != self.cluster_id:
            raise ValueError("snapshot belongs to another cluster or version")
        operations = snapshot.get("operations")
        if not isinstance(operations, dict) or len(operations) > MAX_OPERATIONS:
            raise ValueError("invalid cluster snapshot")
        added = 0
        with self._lock:
            for operation_id, operation in operations.items():
                if operation_id in self._state["operations"]:
                    continue
                if not isinstance(operation, dict) or operation.get("operation_id") != operation_id:
                    raise ValueError("operation identity mismatch")
                if operation.get("operation_hash") != self._operation_hash(operation):
                    raise ValueError("operation integrity check failed")
                if not isinstance(operation.get("payload"), dict):
                    raise ValueError("operation payload must be an object")
                self._state["operations"][operation_id] = operation
                added += 1
            if added:
                self._save()
        return added

    def snapshot(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._state))

    def _ordered_operations(self) -> list[dict]:
        return sorted(self._state["operations"].values(), key=lambda item: (item["timestamp"], item["operation_id"]))

    def materialized(self) -> dict:
        with self._lock:
            queued: dict = {}
            running: dict = {}
            completed: dict = {}
            failed: dict = {}
            contributions: dict = {}
            for operation in self._ordered_operations():
                kind = operation["kind"]
                payload = operation["payload"]
                task_id = payload.get("task_id")
                if kind == "task_submit" and task_id:
                    queued[task_id] = dict(payload["task"])
                elif kind == "lease_claim" and task_id and task_id in queued:
                    running[task_id] = {"task": queued.pop(task_id), **{key: payload[key] for key in ("worker_id", "lease_id", "lease_until")}}
                elif kind == "lease_release" and task_id and task_id in running:
                    queued[task_id] = running.pop(task_id)["task"]
                elif kind == "task_complete" and task_id:
                    record = running.pop(task_id, {"task": payload.get("task", {})})
                    completed[task_id] = {**record, **payload}
                    queued.pop(task_id, None)
                elif kind == "task_fail" and task_id:
                    record = running.pop(task_id, {"task": payload.get("task", {})})
                    failed[task_id] = {**record, **payload}
                    queued.pop(task_id, None)
                elif kind == "contribution":
                    contribution_id = payload.get("contribution_id") or operation["operation_id"]
                    contributions[contribution_id] = dict(payload)
            return {"queued": queued, "running": running, "completed": completed, "failed": failed, "contributions": contributions}

    def queue_task(self, task: dict, origin: str) -> str:
        if not isinstance(task, dict) or not task.get("task_id"):
            raise ValueError("task must contain task_id")
        return self.append("task_submit", {"task_id": task["task_id"], "task": task}, origin)

    def claim_task(self, worker_id: str, origin: str, available_memory_mb: int, cpu_count: int, lease_seconds: int = 300) -> Optional[dict]:
        state = self.materialized()
        for task_id, task in sorted(state["queued"].items(), key=lambda pair: (pair[1].get("created_at", 0), pair[0])):
            if int(task.get("memory_mb", 0)) > available_memory_mb or int(task.get("threads", 1)) > max(1, cpu_count):
                continue
            lease = {"task_id": task_id, "worker_id": worker_id, "lease_id": str(uuid.uuid4()), "lease_until": time.time() + max(1, lease_seconds)}
            self.append("lease_claim", lease, origin)
            return {"task": task, **lease}
        return None

    def release_task(self, task_id: str, lease_id: str, reason: str, origin: str) -> str:
        return self.append("lease_release", {"task_id": task_id, "lease_id": lease_id, "reason": reason}, origin)

    def complete_task(self, task_id: str, lease_id: str, result: dict, verification_result: str, origin: str) -> str:
        if len(_canonical(result).encode("utf-8")) > 1024 * 1024:
            raise ValueError("task result exceeds maximum size")
        return self.append("task_complete", {"task_id": task_id, "lease_id": lease_id, "result": result, "verification_result": verification_result}, origin)

    def fail_task(self, task_id: str, lease_id: str, error: str, origin: str) -> str:
        return self.append("task_fail", {"task_id": task_id, "lease_id": lease_id, "error": str(error)}, origin)

    def record_contribution(self, contribution: Contribution, origin: str) -> str:
        payload = contribution.to_dict()
        payload["contribution_id"] = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
        return self.append("contribution", payload, origin)


class KarmicClusterRuntime:
    """Cluster facade whose leader is always selected by karmic mass."""

    def __init__(self, node_id: str, cluster_id: str = "default", *, karma_path: str = ".zdx/karma.json", state_path: str = ".zdx/cluster_state.json"):
        self.node_id = node_id
        self.cluster_id = cluster_id
        self.karma = ZDXKarmaSystem(karma_path)
        self.state = ReplicatedClusterState(state_path, cluster_id)

    def join(self) -> bool:
        return self.karma.join_cluster(self.node_id, self.cluster_id)

    def master_id(self) -> Optional[str]:
        return self.karma.elect_master_node(self.cluster_id)

    def is_master(self) -> bool:
        return self.master_id() == self.node_id

    def cluster_info(self) -> dict:
        info = self.karma.get_cluster_info(self.cluster_id)
        info["elected_master"] = self.master_id()
        info["replicated_state"] = self.state.materialized()
        return info

    def merge_peer_state(self, snapshot: dict) -> int:
        return self.state.merge(snapshot)
