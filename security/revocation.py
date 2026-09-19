"""Persistent node identity revocation registry."""
from zdx_storage import StateStore


class RevocationRegistry:
    def __init__(self, path=None):
        self.store = StateStore(path, "revocations") if path else None
        data = self.store.load({"revoked": []}) if self.store else {"revoked": []}
        self.revoked = set(data.get("revoked", []))

    def _save(self):
        if self.store:
            self.store.save({"revoked": sorted(self.revoked)})

    def revoke(self, node_id: str):
        self.revoked.add(node_id)
        self._save()

    def restore(self, node_id: str):
        self.revoked.discard(node_id)
        self._save()

    def is_revoked(self, node_id: str) -> bool:
        return node_id in self.revoked
