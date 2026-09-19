"""Deterministic capability-aware node selection for ZDX simulations."""

from __future__ import annotations
import time
import threading
from zdx_metrics import METRICS


class ZDXScheduler:
    """Track nodes and select the strongest candidate deterministically.

    Accelerators are preferred, followed by CPU capacity. Node ID is the
    final tie-breaker so identical inputs always produce identical placement.
    """

    def __init__(self):
        self.nodes: dict[str, dict] = {}
        self._lock = threading.RLock()

    def register_node(self, node_id: str, capabilities: dict) -> None:
        if not isinstance(node_id, str) or not node_id:
            raise ValueError("node_id must be a non-empty string")
        if not isinstance(capabilities, dict):
            raise TypeError("capabilities must be a dictionary")
        with self._lock:
            self.nodes[node_id] = dict(capabilities)

    def remove_node(self, node_id: str) -> bool:
        with self._lock:
            return self.nodes.pop(node_id, None) is not None

    @staticmethod
    def _score(item: tuple[str, dict]) -> tuple[int, int, str]:
        node_id, capabilities = item
        accelerated = int(bool(capabilities.get("gpu") or capabilities.get("npu")))
        cpu_count = capabilities.get("cpu_count", 0)
        if isinstance(cpu_count, bool) or not isinstance(cpu_count, int):
            cpu_count = 0
        return accelerated, max(0, cpu_count), node_id

    def execute_mission(self, agent, mission):
        """Dispatch one local mission without holding the scheduler lock."""
        started = time.perf_counter()
        try:
            execute = getattr(agent, "execute", None)
            if not callable(execute):
                raise TypeError("agent must provide execute(mission)")
            return execute(mission)
        finally:
            METRICS.observe("mission_scheduler_latency", time.perf_counter() - started)

    def select_node(self):
        """Return ``(node_id, capabilities)`` or ``None`` when empty."""
        started = time.perf_counter()
        try:
            with self._lock:
                if not self.nodes:
                    return None
                node_id, capabilities = max(self.nodes.items(), key=self._score)
                return node_id, dict(capabilities)
        finally:
            METRICS.observe("scheduler_latency", time.perf_counter() - started)
