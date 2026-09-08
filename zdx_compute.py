"""Public compute API with durable accounting and guard-file preemption."""

from __future__ import annotations

import os
import signal
import threading
from pathlib import Path

from zdx_compute_core import MAX_RESULT_BYTES, ComputeTask, sha256_file
from zdx_compute_core import ComputeCoordinator as _CoreCoordinator
from zdx_compute_core import execute_task as _execute_core


class ComputeCoordinator(_CoreCoordinator):
    def _ensure_contributions(self):
        if "contributions" not in self._state:
            self._state["contributions"] = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._ensure_contributions()
        self._save()

    def _record(self, task, worker_id, result, verification_result, completed_work, karma_impact, error=""):
        self._ensure_contributions()
        elapsed = float(result.get("elapsed_seconds", 0.0)) if isinstance(result, dict) else 0.0
        metadata = task.get("metadata", {})
        record = {
            "node_id": worker_id,
            "workload_id": str(metadata.get("workload_id", task.get("task_id", ""))),
            "task_id": task.get("task_id"),
            "cpu_time_seconds": max(0.0, elapsed),
            "ram_time_mb_seconds": max(0.0, float(task.get("memory_mb", 0)) * elapsed),
            "task_class": str(metadata.get("task_class", "pyxel_frame")),
            "verification_result": verification_result,
            "completed_work": int(completed_work),
            "karma_impact": int(karma_impact),
            "error": str(error),
        }
        key = f"{task.get('task_id')}:{worker_id}:{len(self._state['contributions'])}"
        self._state["contributions"][key] = record

    def complete(self, worker_id, task_id, result, lease_id=None):
        running = self._state.get("running", {}).get(task_id)
        super().complete(worker_id, task_id, result, lease_id=lease_id)
        if running:
            self._record(running["task"], worker_id, result, "verified", 1, 8)
            self._save()

    def fail(self, worker_id, task_id, error, lease_id=None):
        running = self._state.get("running", {}).get(task_id)
        super().fail(worker_id, task_id, error, lease_id=lease_id)
        if running:
            self._record(running["task"], worker_id, {}, "failed", 0, -2, error)
            self._save()


def execute_task(task: ComputeTask) -> dict:
    """Execute with an optional process-local production guard watchdog."""
    guard_name = os.environ.get("ZDX_PRODUCTION_GUARD", "")
    if not guard_name or not hasattr(signal, "SIGUSR1") or threading.current_thread() is not threading.main_thread():
        return _execute_core(task)
    previous = signal.getsignal(signal.SIGUSR1)
    stop = threading.Event()

    def preempt(_signum, _frame):
        raise RuntimeError("execution preempted by production pause guard")

    def watch():
        while not stop.wait(0.2):
            if Path(guard_name).exists():
                os.kill(os.getpid(), signal.SIGUSR1)
                return

    signal.signal(signal.SIGUSR1, preempt)
    watcher = threading.Thread(target=watch, name="zdx-guard-watch", daemon=True)
    watcher.start()
    try:
        return _execute_core(task)
    finally:
        stop.set()
        watcher.join(timeout=1.0)
        signal.signal(signal.SIGUSR1, previous)
