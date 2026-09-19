"""Versioned persistent legacy node identifier compatibility API."""
import uuid
from pathlib import Path
from zdx_storage import StateStore

class ZDXNodeIdentity:
    def __init__(self, path=".zdx/node_identity.json"):
        self.path = Path(path)
        self.store = StateStore(self.path, "legacy-node-identity")
        self.identity = self.load()
    def load(self):
        data = self.store.load(None)
        if data is not None: return data
        identity = {"node_id": str(uuid.uuid4()), "version": 1}
        self.store.save(identity); return identity
    @property
    def node_id(self): return self.identity["node_id"]
    def payload(self): return dict(self.identity)
