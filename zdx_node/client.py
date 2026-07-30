"""Portable client for the lightweight ZDX coordination protocol.

This module is the canonical home of :class:`ZDXNode`. Keeping the client
inside the package avoids the former ``zdx_node.py``/``zdx_node/`` import
collision while preserving ``from zdx_node import ZDXNode`` for callers.
"""

from __future__ import annotations

import hashlib
import socket
import time
import uuid
from dataclasses import dataclass, field

from zdx_network import ZDXMessage, heartbeat, recv_message, send_message


@dataclass
class ZDXNode:
    """A small client for identity, heartbeat, and frame announcements."""

    host: str = "127.0.0.1"
    port: int = 8765
    node_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    peers: dict = field(default_factory=dict)

    def identity(self) -> ZDXMessage:
        return ZDXMessage(
            kind="identity",
            payload={"node_id": self.node_id, "protocol": 1},
        )

    def connect(self, timeout: float = 5.0) -> socket.socket:
        sock = socket.create_connection((self.host, self.port), timeout=timeout)
        self.peers[self.host] = sock
        send_message(sock, self.identity())
        return sock

    def ping(self, sock: socket.socket) -> ZDXMessage:
        send_message(sock, heartbeat())
        return recv_message(sock)

    @staticmethod
    def hash_frame(path: str) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as frame:
            for chunk in iter(lambda: frame.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def announce_frame(self, path: str) -> ZDXMessage:
        return ZDXMessage(
            kind="frame_manifest",
            payload={
                "path": path,
                "sha256": self.hash_frame(path),
                "node_id": self.node_id,
                "created": time.time(),
            },
        )
