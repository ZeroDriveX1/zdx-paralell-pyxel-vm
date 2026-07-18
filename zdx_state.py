"""
Persistent state helpers for ZDX Pyxel nodes.

Stores node metadata and peer observations locally so a node can restart
without losing identity information.
"""

from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone


class ZDXState:
    def __init__(self, path=".zdx/node_state.json"):
        self.path = Path(path)
        self.data = self._load()

    def _load(self):
        if self.path.exists():
            return json.loads(self.path.read_text())
        return {"created": self.now(), "peers": {}, "heartbeats": 0}

    def now(self):
        return datetime.now(timezone.utc).isoformat()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2))

    def record_peer(self, address, identity):
        self.data["peers"][str(address)] = {
            "identity": identity,
            "last_seen": self.now(),
        }
        self.save()

    def record_heartbeat(self):
        self.data["heartbeats"] = self.data.get("heartbeats", 0) + 1
        self.data["last_heartbeat"] = self.now()
        self.save()
