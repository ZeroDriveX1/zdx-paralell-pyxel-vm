"""Compatibility helpers for the canonical ZDX protocol version."""

from dataclasses import dataclass
from zdx_network import PROTOCOL_VERSION

CURRENT_PROTOCOL_VERSION = PROTOCOL_VERSION


@dataclass(frozen=True)
class VersionHandshake:
    node_id: str
    protocol_version: int


def compatible(version: int) -> bool:
    return version == PROTOCOL_VERSION
