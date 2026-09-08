"""Portable authenticated and TLS-capable ZDX coordination client."""

from __future__ import annotations

import hashlib
import socket
import ssl
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from zdx_network import ZDXMessage, heartbeat, recv_message, send_message


@dataclass
class ZDXNode:
    host: str = "127.0.0.1"
    port: int = 8765
    node_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    peers: dict = field(default_factory=dict)
    signer: Optional[Any] = None
    tls_context: Optional[ssl.SSLContext] = None
    server_hostname: Optional[str] = None
    _next_sequence: int = field(default=1, init=False, repr=False)

    def _prepare_message(self, message: ZDXMessage) -> ZDXMessage:
        if self.signer is None:
            return message
        message.peer_id = self.node_id
        message.sequence = self._next_sequence
        self._next_sequence += 1
        message.sign(self.signer)
        return message

    def identity(self) -> ZDXMessage:
        message = ZDXMessage(kind="identity", payload={"node_id": self.node_id, "protocol": 1})
        if self.signer is not None:
            message.payload["public_key"] = self.signer.get_public_key_pem()
            message.payload["key_version"] = self.signer.get_key_version()
        return self._prepare_message(message)

    def connect(self, timeout: float = 5.0) -> socket.socket:
        sock = socket.create_connection((self.host, self.port), timeout=timeout)
        if self.tls_context is not None:
            sock = self.tls_context.wrap_socket(sock, server_hostname=self.server_hostname or self.host)
        self.peers[self.host] = sock
        try:
            send_message(sock, self.identity())
            identity_ack = recv_message(sock)
        except Exception:
            self.peers.pop(self.host, None)
            sock.close()
            raise
        if identity_ack.kind != "identity_ack":
            self.peers.pop(self.host, None)
            sock.close()
            raise ConnectionError(f"peer rejected identity: {identity_ack.payload}")
        return sock

    def ping(self, sock: socket.socket) -> ZDXMessage:
        return self.request(sock, heartbeat())

    def request(self, sock: socket.socket, message: ZDXMessage) -> ZDXMessage:
        send_message(sock, self._prepare_message(message))
        return recv_message(sock)

    @staticmethod
    def hash_frame(path: str) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as frame:
            for chunk in iter(lambda: frame.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def announce_frame(self, path: str) -> ZDXMessage:
        return self._prepare_message(ZDXMessage(kind="frame_manifest", payload={
            "path": path, "sha256": self.hash_frame(path), "node_id": self.node_id, "created": time.time(),
        }))
