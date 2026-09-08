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
import math
import socket
import struct
import time
import uuid
from dataclasses import dataclass, asdict, fields
from typing import Any, Optional


PROTOCOL_VERSION = 1
MAX_MESSAGE_SIZE = 16 * 1024 * 1024


@dataclass
class ZDXMessage:
    kind: str
    payload: dict
    request_id: str = ""
    version: int = PROTOCOL_VERSION
    timestamp: float = 0.0
    peer_id: str = ""
    signature: str = ""
    sequence: Optional[int] = None

    def auth_payload(self) -> dict:
        """Return the complete envelope covered by an Ed25519 signature."""
        return {
            "kind": self.kind,
            "payload": self.payload,
            "request_id": self.request_id,
            "version": self.version,
            "timestamp": self.timestamp,
            "peer_id": self.peer_id,
            "sequence": self.sequence,
        }

    def sign(self, signer: Any) -> None:
        """Sign this message using a signer with ``sign_message``."""
        self._prepare()
        self.signature = signer.sign_message(self.auth_payload())

    def _prepare(self) -> None:
        if not self.request_id:
            self.request_id = str(uuid.uuid4())
        if not self.timestamp:
            self.timestamp = time.time()

    def encode(self) -> bytes:
        self._prepare()
        self._validate()
        body = json.dumps(asdict(self), separators=(",", ":")).encode("utf-8")
        if len(body) > MAX_MESSAGE_SIZE:
            raise ValueError("message exceeds maximum size")
        return struct.pack(">I", len(body)) + body

    @staticmethod
    def decode(body: bytes) -> "ZDXMessage":
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("message body is not valid JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("message body must be a JSON object")

        allowed = {item.name for item in fields(ZDXMessage)}
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"unknown message fields: {sorted(unknown)}")
        try:
            message = ZDXMessage(**data)
            message._validate()
            return message
        except TypeError as exc:
            raise ValueError("message fields are malformed") from exc

    def _validate(self) -> None:
        if not isinstance(self.kind, str) or not self.kind:
            raise ValueError("message kind must be a non-empty string")
        if not isinstance(self.payload, dict):
            raise ValueError("message payload must be an object")
        if isinstance(self.version, bool) or not isinstance(self.version, int):
            raise ValueError("message version must be an integer")
        if not isinstance(self.request_id, str):
            raise ValueError("request_id must be a string")
        if isinstance(self.timestamp, bool) or not isinstance(self.timestamp, (int, float)):
            raise ValueError("timestamp must be numeric")
        if not math.isfinite(float(self.timestamp)):
            raise ValueError("timestamp must be finite")
        if not isinstance(self.peer_id, str) or not isinstance(self.signature, str):
            raise ValueError("peer_id and signature must be strings")
        if self.sequence is not None and (
            isinstance(self.sequence, bool) or not isinstance(self.sequence, int)
        ):
            raise ValueError("sequence must be an integer or null")


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
