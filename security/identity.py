"""Persistent Ed25519 identity using transactional secret storage."""
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from zdx_storage import StateStore, atomic_write_secret


@dataclass(frozen=True)
class NodeIdentity:
    private_key: Ed25519PrivateKey

    @property
    def public_key_bytes(self):
        return self.private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )

    @property
    def node_id(self):
        return sha256(self.public_key_bytes).hexdigest()

    @classmethod
    def load_or_create(cls, path: str):
        key_path = Path(path)
        if key_path.exists():
            private = serialization.load_pem_private_key(
                key_path.read_bytes(), password=None
            )
        else:
            private = Ed25519PrivateKey.generate()
            atomic_write_secret(key_path, private.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ))
        identity = cls(private)
        StateStore(
            str(key_path) + ".metadata.json", "identity-metadata"
        ).save({
            "node_id": identity.node_id,
            "algorithm": "Ed25519",
            "public_key_sha256": identity.node_id,
        })
        return identity
