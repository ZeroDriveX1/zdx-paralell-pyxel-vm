"""Security primitives for Open-Pyxel networking."""

import hashlib
import json


def hash_payload(payload: dict) -> str:
    """Create deterministic payload hash for verification."""
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_message(message: dict) -> bool:
    """Basic protocol validation hook.

    Signature verification and replay protection will be added here.
    """
    required = {"message_type", "payload", "timestamp", "sender_id", "signature"}
    return required.issubset(message.keys())
