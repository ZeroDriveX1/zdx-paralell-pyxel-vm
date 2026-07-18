"""Peer-to-peer discovery primitives for Open-Pyxel."""

from dataclasses import dataclass, field
import time


@dataclass
class Peer:
    node_id: str
    address: str
    capabilities: dict
    last_seen: int = field(default_factory=lambda: int(time.time()))


class PeerRegistry:
    def __init__(self):
        self.peers: dict[str, Peer] = {}

    def add_peer(self, peer: Peer) -> None:
        self.peers[peer.node_id] = peer

    def remove_peer(self, node_id: str) -> None:
        self.peers.pop(node_id, None)

    def active_peers(self) -> list[Peer]:
        return list(self.peers.values())
