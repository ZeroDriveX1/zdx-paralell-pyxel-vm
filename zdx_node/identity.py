"""Authenticated node identity primitives.

Provides Ed25519 identity generation, persistence, and signing.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


@dataclass
class NodeIdentity:
    node_id: str
    private_key_path: Path
    private_key: Ed25519PrivateKey

    @classmethod
    def load_or_create(cls, path: str = "~/.zdx/node_identity.json") -> "NodeIdentity":
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)

        if target.exists():
            data = json.loads(target.read_text())
            key = Ed25519PrivateKey.from_private_bytes(
                base64.b64decode(data["private_key"])
            )
            return cls(data["node_id"], target, key)

        key = Ed25519PrivateKey.generate()
        raw = key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        node_id = base64.urlsafe_b64encode(os.urandom(12)).decode().rstrip("=")
        target.write_text(json.dumps({
            "node_id": node_id,
            "private_key": base64.b64encode(raw).decode(),
        }, indent=2))
        return cls(node_id, target, key)

    def public_key(self) -> str:
        raw = self.private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        return base64.b64encode(raw).decode()

    def sign(self, payload: bytes) -> str:
        return base64.b64encode(self.private_key.sign(payload)).decode()
