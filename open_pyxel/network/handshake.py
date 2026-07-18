"""Secure node handshake primitives."""

from dataclasses import dataclass
import time


@dataclass
class NodeHandshake:
    node_id: str
    challenge: str
    timestamp: int


def create_handshake(node_id: str, challenge: str) -> NodeHandshake:
    return NodeHandshake(
        node_id=node_id,
        challenge=challenge,
        timestamp=int(time.time()),
    )


def validate_handshake(handshake: NodeHandshake) -> bool:
    return bool(handshake.node_id and handshake.challenge)
