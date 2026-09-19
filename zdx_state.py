"""Transactional persistent coordinator state."""
from datetime import datetime, timezone
from pathlib import Path
from zdx_storage import StateStore


class ZDXState:
    def __init__(self, path=".zdx/node_state.json"):
        self.path = Path(path)
        self.store = StateStore(self.path, "coordinator-state")
        self.data = self.store.load(self._default())

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat()

    def _default(self):
        return {"created": self.now(), "peers": {}, "heartbeats": 0}

    def save(self):
        self.store.save(self.data)

    def record_peer(self, address, identity):
        def update(data):
            data = dict(data or self._default())
            peers = dict(data.get("peers", {}))
            peers[str(address)] = {"identity": identity, "last_seen": self.now()}
            data["peers"] = peers
            return data
        self.data = self.store.update(update, self._default())

    def record_heartbeat(self):
        def update(data):
            data = dict(data or self._default())
            data["heartbeats"] = data.get("heartbeats", 0) + 1
            data["last_heartbeat"] = self.now()
            return data
        self.data = self.store.update(update, self._default())
