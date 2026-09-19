"""Thread-safe authenticated coordinator node registry."""

from __future__ import annotations

import threading
import time

from zdx_network import ProtocolError
from zdx_storage import StateStore
from zdx_metrics import METRICS


class ZDXNodeRegistry:
    """Tracks one active authenticated session for each enrolled identity."""

    def __init__(self, stale_after: float = 90.0, path=None,
                 failure_injector=None):
        if stale_after <= 0:
            raise ValueError("stale_after must be positive")
        self.stale_after = stale_after
        self._store = StateStore(path, "node-registry") if path else None
        saved = self._store.load({"nodes": {}}) if self._store else {"nodes": {}}
        self.nodes: dict[str, dict] = saved.get("nodes", {})
        self._session_nodes: dict[str, str] = {}
        self._lock = threading.RLock()
        self.failure_injector = failure_injector
        for node_id, record in self.nodes.items():
            if record.get("session_id"):
                self._session_nodes[record["session_id"]] = node_id

    def _persist(self):
        METRICS.gauge("connected_nodes", sum(
            bool(record.get("session_id")) for record in self.nodes.values()
        ))
        if self.failure_injector:
            self.failure_injector.hit("registry.before_persist")
        if self._store:
            self._store.save({"nodes": self.nodes})

    def register(self, node_id, capabilities, session_id: str = "",
                 key_id: str = "1", now: float | None = None):
        if not node_id:
            raise ProtocolError("identity", "node ID is required")
        if not isinstance(capabilities, dict):
            raise ProtocolError("payload", "capabilities must be an object")
        current = time.time() if now is None else now
        with self._lock:
            owner = self._session_nodes.get(session_id) if session_id else None
            if owner and owner != node_id:
                raise ProtocolError("identity", "session is bound to another node")
            existing = self.nodes.get(node_id)
            reconnects = 0
            if existing:
                if existing["key_id"] != key_id:
                    raise ProtocolError("identity", "duplicate node ID with another key")
                reconnects = existing["reconnects"]
                old_session = existing["session_id"]
                if session_id and old_session and old_session != session_id:
                    reconnects += 1
                    self._session_nodes.pop(old_session, None)
            record = {
                "capabilities": dict(capabilities),
                "last_seen": current,
                "registered_at": (
                    existing["registered_at"] if existing else current
                ),
                "session_id": session_id,
                "key_id": key_id,
                "reconnects": reconnects,
            }
            self.nodes[node_id] = record
            if session_id:
                self._session_nodes[session_id] = node_id
            self._persist()
            return dict(record)

    def heartbeat(self, node_id, session_id: str = "",
                  now: float | None = None) -> bool:
        current = time.time() if now is None else now
        with self._lock:
            record = self.nodes.get(node_id)
            if record is None:
                return False
            if session_id and record["session_id"] != session_id:
                raise ProtocolError("session", "heartbeat session is not active")
            record["last_seen"] = current
            self._persist()
            return True

    def remove_stale(self, now: float | None = None) -> list[str]:
        current = time.time() if now is None else now
        with self._lock:
            stale = sorted(
                node_id for node_id, record in self.nodes.items()
                if current - record["last_seen"] > self.stale_after
            )
            for node_id in stale:
                session_id = self.nodes[node_id]["session_id"]
                self.nodes.pop(node_id, None)
                if session_id:
                    self._session_nodes.pop(session_id, None)
            self._persist()
            return stale

    def disconnect(self, session_id: str) -> None:
        with self._lock:
            node_id = self._session_nodes.pop(session_id, None)
            if node_id and node_id in self.nodes:
                self.nodes[node_id]["session_id"] = ""
            self._persist()

    def get(self, node_id):
        with self._lock:
            record = self.nodes.get(node_id)
            return dict(record) if record else None

    def all_nodes(self):
        with self._lock:
            return {key: dict(value) for key, value in self.nodes.items()}
