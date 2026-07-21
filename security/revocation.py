"""Node identity revocation registry."""


class RevocationRegistry:
    def __init__(self):
        self.revoked = set()

    def revoke(self, node_id: str):
        self.revoked.add(node_id)

    def restore(self, node_id: str):
        self.revoked.discard(node_id)

    def is_revoked(self, node_id: str) -> bool:
        return node_id in self.revoked
