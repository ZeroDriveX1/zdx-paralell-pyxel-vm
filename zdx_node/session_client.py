"""Authenticated client for the canonical ZDX coordination protocol."""

from __future__ import annotations

import hashlib
import socket
import ssl
from dataclasses import dataclass, field

from security.identity import NodeIdentity
from zdx_network import TrustedKeyStore, ZDXMessage, recv_message, send_message
from zdx_session import NodeCredentials, SessionClient
from zdx_tls import TLSConfig


@dataclass
class SessionZDXNode:
    host: str = "127.0.0.1"
    port: int = 8765
    key_path: str = ".zdx/node_key.pem"
    credentials: NodeCredentials | None = None
    coordinator_id: str = ""
    trust: TrustedKeyStore = field(default_factory=TrustedKeyStore)
    peers: dict = field(default_factory=dict)
    capabilities: dict = field(default_factory=dict)
    tls_config: TLSConfig | None = None
    server_hostname: str = ""
    allow_insecure: bool = False
    _session: SessionClient | None = field(default=None, init=False)

    def __post_init__(self):
        if self.credentials is None:
            self.credentials = NodeCredentials(
                NodeIdentity.load_or_create(self.key_path)
            )

    @property
    def node_id(self) -> str:
        return self.credentials.node_id

    def trust_coordinator(self, node_id: str, public_key: bytes,
                          key_id: str = "1") -> None:
        self.trust.enroll(node_id, public_key, key_id)
        self.coordinator_id = node_id

    def identity(self) -> ZDXMessage:
        if self._session is None:
            raise RuntimeError("authenticated session is not established")
        return self._session.message(
            "identity", {
                "node_id": self.node_id,
                "capabilities": dict(self.capabilities),
            }
        )

    def connect(self, timeout: float = 5.0) -> socket.socket:
        if not self.coordinator_id or not self.trust.is_enrolled(
            self.coordinator_id
        ):
            raise RuntimeError("coordinator public key must be pinned first")
        raw_sock = socket.create_connection((self.host, self.port), timeout=timeout)
        if self.tls_config is not None:
            hostname = self.server_hostname or self.host
            try:
                sock = self.tls_config.client_context().wrap_socket(
                    raw_sock, server_hostname=hostname
                )
            except Exception:
                raw_sock.close()
                raise
        elif self.allow_insecure:
            sock = raw_sock
        else:
            raw_sock.close()
            raise RuntimeError("TLS configuration is required")
        session = SessionClient(self.credentials, self.trust)
        send_message(sock, session.hello())
        send_message(sock, session.confirm(recv_message(sock), self.coordinator_id))
        session.accept_ack(recv_message(sock))
        self._session = session
        self.peers[self.host] = sock
        send_message(sock, self.identity())
        session.validate_response(recv_message(sock))
        return sock

    def ping(self, sock: socket.socket) -> ZDXMessage:
        if self._session is None:
            raise RuntimeError("authenticated session is not established")
        send_message(sock, self._session.message("heartbeat", {"status": "alive"}))
        return self._session.validate_response(recv_message(sock))

    @staticmethod
    def hash_frame(path: str) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as frame:
            for chunk in iter(lambda: frame.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def announce_frame(self, path: str) -> ZDXMessage:
        if self._session is None:
            raise RuntimeError("authenticated session is not established")
        return self._session.message(
            "frame_manifest",
            {"path": path, "sha256": self.hash_frame(path)},
        )
