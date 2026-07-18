"""
ZDX persistent node identity.

Keeps a stable node identifier across restarts.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path


class ZDXNodeIdentity:
    def __init__(self, path=".zdx/node_identity.json"):
        self.path = Path(path)
        self.identity = self.load()

    def load(self):
        if self.path.exists():
            return json.loads(self.path.read_text())
        identity = {
            "node_id": str(uuid.uuid4()),
            "version": 1,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(identity, indent=2))
        return identity

    @property
    def node_id(self):
        return self.identity["node_id"]

    def payload(self):
        return self.identity
