"""Compatibility identity API backed by canonical transactional key storage."""
from dataclasses import dataclass
from pathlib import Path
from security.identity import NodeIdentity as CanonicalIdentity

@dataclass
class NodeIdentity:
    node_id: str
    private_key_path: Path
    private_key: object
    @classmethod
    def load_or_create(cls, path: str = "~/.zdx/node_identity.json"):
        metadata_path = Path(path).expanduser()
        key_path = metadata_path.with_suffix(".key.pem")
        identity = CanonicalIdentity.load_or_create(str(key_path))
        return cls(identity.node_id, key_path, identity.private_key)
    def public_key(self):
        import base64
        from cryptography.hazmat.primitives import serialization
        raw = self.private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        return base64.b64encode(raw).decode()
    def sign(self, payload: bytes):
        import base64
        return base64.b64encode(self.private_key.sign(payload)).decode()
