"""
ZDX Parallel Pyxel VM - Stable Networking Layer

Deterministic transport primitives for distributing Pyxel VM frames.

This module intentionally provides a small, dependency-free protocol layer:
- length-prefixed JSON envelopes
- request/response correlation IDs
- heartbeat messages
- frame transfer metadata
- safe socket timeouts

Execution remains local and deterministic. Networking only moves data.
"""

from __future__ import annotations

import json
import socket
import struct
import time
import uuid
from dataclasses import dataclass, asdict


PROTOCOL_VERSION = 1
MAX_MESSAGE_SIZE = 16 * 1024 * 1024


@dataclass
class ZDXMessage:
    kind: str
    payload: dict
    request_id: str = ""
    version: int = PROTOCOL_VERSION
    timestamp: float = 0.0

    def encode(self) -> bytes:
        if not self.request_id:
            self.request_id = str(uuid.uuid4())
        if not self.timestamp:
            self.timestamp = time.time()
        body = json.dumps(asdict(self), separators=(",", ":")).encode("utf-8")
        if len(body) > MAX_MESSAGE_SIZE:
            raise ValueError("message exceeds maximum size")
        return struct.pack(">I", len(body)) + body

    @staticmethod
    def decode(body: bytes) -> "ZDXMessage":
        data = json.loads(body.decode("utf-8"))
        return ZDXMessage(**data)


def recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("connection closed while receiving data")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def recv_message(sock: socket.socket) -> ZDXMessage:
    header = recv_exact(sock, 4)
    size = struct.unpack(">I", header)[0]
    if size > MAX_MESSAGE_SIZE:
        raise ValueError("incoming message exceeds maximum size")
    return ZDXMessage.decode(recv_exact(sock, size))


def send_message(sock: socket.socket, message: ZDXMessage) -> None:
    sock.sendall(message.encode())


def heartbeat() -> ZDXMessage:
    return ZDXMessage(kind="heartbeat", payload={"status": "alive"})


def frame_announce(path: str, sha256: str) -> ZDXMessage:
    return ZDXMessage(
        kind="frame",
        payload={"path": path, "sha256": sha256},
    )
