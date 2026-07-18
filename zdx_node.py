"""
ZDX Parallel Pyxel VM node layer.

Provides a lightweight peer abstraction around zdx_network transport.
Networking does not execute remote code; it only coordinates deterministic
frame/state exchange.
"""

from __future__ import annotations

import hashlib
import socket
import time
import uuid
from dataclasses import dataclass, field

from zdx_network import ZDXMessage, heartbeat, send_message, recv_message


@dataclass
class ZDXNode:
    host: str = "127.0.0.1"
    port: int = 8765
    node_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    peers: dict = field(default_factory=dict)

    def identity(self) -> ZDXMessage:
        return ZDXMessage(
            kind="identity",
            payload={
                "node_id": self.node_id,
                "protocol": 1,
            },
        )

    def connect(self, timeout: float = 5.0):
        sock = socket.create_connection((self.host, self.port), timeout=timeout)
        self.peers[self.host] = sock
        send_message(sock, self.identity())
        return sock

    def ping(self, sock):
        send_message(sock, heartbeat())
        return recv_message(sock)

    @staticmethod
    def hash_frame(path: str) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as frame:
            for chunk in iter(lambda: frame.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def announce_frame(self, path: str):
        return ZDXMessage(
            kind="frame_manifest",
            payload={
                "path": path,
                "sha256": self.hash_frame(path),
                "node_id": self.node_id,
                "created": time.time(),
            },
        )
