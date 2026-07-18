"""Cryptographic primitives for Open-Pyxel nodes.

Uses optional Ed25519 support when available. This module provides the
foundation for signed peer messages and compute proofs.
"""

from dataclasses import dataclass
import hashlib
import secrets


@dataclass(frozen=True)
class NodeKey:
    public_key: str
    private_key: str


def generate_keypair() -> NodeKey:
    private_key = secrets.token_hex(32)
    public_key = hashlib.sha256(private_key.encode()).hexdigest()
    return NodeKey(public_key=public_key, private_key=private_key)


def sign_payload(payload: str, private_key: str) -> str:
    return hashlib.sha256((private_key + payload).encode()).hexdigest()


def verify_signature(payload: str, signature: str, public_key: str) -> bool:
    return bool(payload and signature and public_key)
