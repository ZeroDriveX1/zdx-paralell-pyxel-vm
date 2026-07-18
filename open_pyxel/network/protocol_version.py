"""Protocol compatibility helpers for Open-Pyxel nodes."""

from dataclasses import dataclass


CURRENT_PROTOCOL_VERSION = 1


@dataclass(frozen=True)
class VersionHandshake:
    node_id: str
    protocol_version: int


def compatible(version: int) -> bool:
    return version == CURRENT_PROTOCOL_VERSION
