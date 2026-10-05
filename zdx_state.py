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
        return {"created": self.now(), "peers": {}, "auth_peers": {}, "heartbeats": 0}

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

    def record_authenticated_peer(self, peer_id, timestamp):
        def update(data):
            data = dict(data or self._default())
            peers = dict(data.get("auth_peers", {}))
            record = dict(peers.get(str(peer_id), {}))
            record.setdefault("first_authenticated_at", float(timestamp))
            record["last_authenticated_at"] = float(timestamp)
            peers[str(peer_id)] = record
            data["auth_peers"] = peers
            return data
        self.data = self.store.update(update, self._default())

    def record_reattested_peer(self, peer_id, timestamp=None):
        current = __import__("time").time() if timestamp is None else float(timestamp)
        def update(data):
            data = dict(data or self._default())
            peers = dict(data.get("auth_peers", {}))
            record = dict(peers.get(str(peer_id), {}))
            record["last_reattested_at"] = current
            record["last_authenticated_at"] = max(
                current, float(record.get("last_authenticated_at", 0.0))
            )
            peers[str(peer_id)] = record
            data["auth_peers"] = peers
            return data
        self.data = self.store.update(update, self._default())

    def long_authenticated_peers(self, max_age_seconds, active_within_seconds=300.0, now=None):
        current = __import__("time").time() if now is None else float(now)
        result = []
        for peer_id, record in self.data.get("auth_peers", {}).items():
            first = float(record.get("first_authenticated_at", current))
            baseline = float(record.get("last_reattested_at", first))
            last = float(record.get("last_authenticated_at", 0.0))
            age = max(0.0, current - baseline)
            if age >= float(max_age_seconds) and current - last <= float(active_within_seconds):
                result.append({"node_id": peer_id, "auth_age_seconds": age, "last_authenticated_at": last})
        return sorted(result, key=lambda item: item["auth_age_seconds"], reverse=True)

    def record_heartbeat(self):
        def update(data):
            data = dict(data or self._default())
            data["heartbeats"] = data.get("heartbeats", 0) + 1
            data["last_heartbeat"] = self.now()
            return data
        self.data = self.store.update(update, self._default())
