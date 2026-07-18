"""Identity helpers for Open-Pyxel network nodes."""

import hashlib
import secrets


def generate_node_identity() -> dict[str, str]:
    """Create a placeholder node identity.

    Cryptographic signing/key storage will be integrated with the final
    key management layer.
    """
    seed = secrets.token_hex(32)
    node_id = hashlib.sha256(seed.encode()).hexdigest()

    return {
        "node_id": node_id,
        "private_key_seed": seed,
    }
