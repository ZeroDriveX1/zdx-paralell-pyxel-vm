"""Signed Open-Pyxel network message protocol definitions."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class NetworkMessage:
    """Base authenticated network message."""

    message_type: str
    payload: dict[str, Any]
    timestamp: int
    sender_id: str
    signature: str


NODE_JOIN = "NODE_JOIN"
TASK_ASSIGN = "TASK_ASSIGN"
COMPUTE_PROOF = "COMPUTE_PROOF"
