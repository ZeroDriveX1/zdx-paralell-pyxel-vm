"""Bounded distributed frame execution and memory-aware task scheduling."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional


MAX_RESULT_BYTES = 1024 * 1024


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class ComputeTask:
    """A verified frame request.

    A task may reference a private content-addressed artifact.  ``frame_path``
    is then empty until a worker materializes that artifact in its private
    working directory.  The legacy shared-path form remains supported.
    """

    frame_path: str = ""
    frame_sha256: str = ""
    artifact_digest: Optional[str] = None
    memory_mb: int = 256
    threads: int = 1
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    max_seconds: int = 300
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> "ComputeTask":
        if not self.task_id or (not self.frame_path and not self.artifact_digest):
            raise ValueError("task_id and frame_path or artifact_digest are required")
        self.frame_sha256 = (self.frame_sha256 or "").lower()
        if self.frame_sha256 and (
            len(self.frame_sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.frame_sha256)
        ):
            raise ValueError("frame_sha256 must be a 64-character hex digest")
        if self.artifact_digest is not None:
            self.artifact_digest = self.artifact_digest.lower()
            if len(self.artifact_digest) != 64 or any(
                char not in "0123456789abcdef" for char in self.artifact_digest
            ):
                raise ValueError("artifact_digest must be a 64-character hex digest")
            if self.frame_sha256 and self.frame_sha256 != self.artifact_digest:
                raise ValueError("frame_sha256 and artifact_digest must match")
            self.frame_sha256 = self.artifact_digest
        if self.memory_mb < 1 or self.threads < 1 or self.max_seconds < 1:
            raise ValueError("memory_mb, threads, and max_seconds must be positive")
        if not isinstance(self.metadata, dict):
            raise ValueError("metadata must be an object")
        return self

    @classmethod
    def from_dict(cls, data: dict) -> "ComputeTask":
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"unknown task fields: {sorted(unknown)}")
        return cls(**data).validate()

    def to_dict(self) -> dict:
        return asdict(self)


class ComputeCoordinator:
    """Persistent task queue with worker leases and RAM admission checks."""

    def __init__(self, state_path: str = ".zdx/compute_state.json", lease_seconds: int = 300):
        self.state_path = Path(state_path)
        self.lease_seconds = max(1, lease_seconds)
        self._lock = threading.RLock()
        self._state = self._load()

    def _load(self) -> dict:
        if not self.state_path.exists():
            return {"version": 1, "queued": {}, "running": {}, "completed": {}, "failed": {}, "workers": {}}
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            if state.get("version") != 1:
                raise ValueError("unsupported compute state version")
            for name in ("queued", "running", "completed", "failed", "workers"):
                if not isinstance(state.get(name), dict):
                    raise ValueError(f"compute state field {name} must be an object")
            return state
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"invalid compute state {self.state_path}: {exc}") from exc

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.state_path.name}.", dir=str(self.state_path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(self._state, stream, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self.state_path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def submit(self, task: ComputeTask) -> ComputeTask:
        task.validate()
        with self._lock:
            if any(task.task_id in self._state[name] for name in ("queued", "running", "completed", "failed")):
                raise ValueError(f"task already exists: {task.task_id}")
            self._state["queued"][task.task_id] = task.to_dict()
            self._save()
        return task

    def submit_frame(self, frame_path: str, **kwargs) -> ComputeTask:
        return self.submit(ComputeTask(frame_path=frame_path, frame_sha256=sha256_file(frame_path), **kwargs))

    def register_worker(self, worker_id: str, capabilities: dict) -> None:
        if not worker_id or not isinstance(capabilities, dict):
            raise ValueError("worker_id and capabilities are required")
        with self._lock:
            self._state["workers"][worker_id] = {"capabilities": dict(capabilities), "last_seen": time.time()}
            self._save()

    def claim(self, worker_id: str, available_memory_mb: int, cpu_count: int = 1) -> Optional[ComputeTask]:
        with self._lock:
            self._requeue_expired_locked()
            worker = self._state["workers"].get(worker_id)
            if worker is None:
                raise ValueError(f"worker is not registered: {worker_id}")
            worker["last_seen"] = time.time()
            candidates = sorted(self._state["queued"].values(), key=lambda item: (item["created_at"], item["task_id"]))
            for raw in candidates:
                task = ComputeTask.from_dict(raw)
                if task.memory_mb > available_memory_mb or task.threads > max(1, cpu_count):
                    continue
                del self._state["queued"][task.task_id]
                now = time.time()
                self._state["running"][task.task_id] = {
                    "task": task.to_dict(), "worker_id": worker_id, "leased_at": now,
                    "lease_until": now + min(self.lease_seconds, task.max_seconds),
                }
                self._save()
                return task
            self._save()
            return None

    def complete(self, worker_id: str, task_id: str, result: dict) -> None:
        if len(json.dumps(result, separators=(",", ":")).encode("utf-8")) > MAX_RESULT_BYTES:
            raise ValueError("task result exceeds maximum size")
        with self._lock:
            running = self._state["running"].get(task_id)
            if running is None or running["worker_id"] != worker_id:
                raise ValueError("task is not leased to this worker")
            self._state["running"].pop(task_id)
            self._state["completed"][task_id] = {
                "task": running["task"], "worker_id": worker_id, "completed_at": time.time(), "result": result,
            }
            self._save()

    def release(self, worker_id: str, task_id: str, reason: str) -> None:
        with self._lock:
            running = self._state["running"].get(task_id)
            if running is None or running["worker_id"] != worker_id:
                raise ValueError("task is not leased to this worker")
            task = dict(running["task"])
            task["metadata"] = {**task.get("metadata", {}), "last_release_reason": reason}
            self._state["running"].pop(task_id)
            self._state["queued"][task_id] = task
            self._save()

    def fail(self, worker_id: str, task_id: str, error: str) -> None:
        with self._lock:
            running = self._state["running"].get(task_id)
            if running is None or running["worker_id"] != worker_id:
                raise ValueError("task is not leased to this worker")
            self._state["running"].pop(task_id)
            self._state["failed"][task_id] = {
                "task": running["task"], "worker_id": worker_id, "failed_at": time.time(), "error": str(error),
            }
            self._save()

    def status(self) -> dict:
        with self._lock:
            self._requeue_expired_locked()
            return json.loads(json.dumps(self._state))

    def _requeue_expired_locked(self) -> None:
        now = time.time()
        for task_id in [key for key, record in self._state["running"].items() if record["lease_until"] <= now]:
            record = self._state["running"].pop(task_id)
            self._state["queued"][task_id] = record["task"]


def execute_task(task: ComputeTask) -> dict:
    """Verify and execute one local deterministic VM frame."""
    task.validate()
    if not task.frame_path or not os.path.isfile(task.frame_path):
        raise FileNotFoundError(task.frame_path)
    actual_hash = sha256_file(task.frame_path)
    if task.frame_sha256 and actual_hash != task.frame_sha256:
        raise ValueError(f"frame hash mismatch: expected {task.frame_sha256}, got {actual_hash}")
    from zdx_parallel_vm import ParallelPyxelVM

    started = time.time()
    vm = ParallelPyxelVM(threads=task.threads)
    previous_handler = None
    previous_timer = None
    timed = threading.current_thread() is threading.main_thread() and hasattr(signal, "SIGALRM")
    if timed:
        previous_handler = signal.getsignal(signal.SIGALRM)
        previous_timer = signal.getitimer(signal.ITIMER_REAL)

        def timeout_handler(_signum, _frame):
            raise TimeoutError(f"task exceeded max_seconds={task.max_seconds}")

        signal.signal(signal.SIGALRM, timeout_handler)
        signal.setitimer(signal.ITIMER_REAL, task.max_seconds)
    try:
        vm.execute_texture(task.frame_path)
        return {"task_id": task.task_id, "registers": vm.registers, "shared": vm.shared, "elapsed_seconds": time.time() - started}
    finally:
        if timed:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)
            if previous_timer and previous_timer[0] > 0:
                signal.setitimer(signal.ITIMER_REAL, previous_timer[0], previous_timer[1])
