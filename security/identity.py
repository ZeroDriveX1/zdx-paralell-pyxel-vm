"""Persistent Ed25519 node identity."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


@dataclass(frozen=True)
class NodeIdentity:
    private_key: Ed25519PrivateKey

    @property
    def public_key_bytes(self) -> bytes:
        return self.private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )

    @property
    def node_id(self) -> str:
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
            key_path.parent.mkdir(parents=True, exist_ok=True)
            key_path.write_bytes(
                private.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
            key_path.chmod(0o600)
        return cls(private)
