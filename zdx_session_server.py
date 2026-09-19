"""Authenticated ZDX coordinator TCP server.

The server never dispatches an unsigned packet.  Enrollment (pinning trusted
node public keys) is an operator action performed before a connection.
"""

from __future__ import annotations

import socket
import ssl
import threading
from pathlib import Path

from security.identity import NodeIdentity
from zdx_network import (
    MessageAuthenticator,
    ProtocolError,
    SessionRegistry,
    TrustedKeyStore,
    ZDXMessage,
    recv_message,
    send_message,
)
from zdx_node_registry import ZDXNodeRegistry
from zdx_session import NodeCredentials, SessionCoordinator
from zdx_state import ZDXState
from zdx_tls import TLSConfig
from zdx_scheduler import ZDXScheduler


class SessionZDXServer:
    def __init__(
        self,
        host="0.0.0.0",
        port=8765,
        credentials: NodeCredentials | None = None,
        trust: TrustedKeyStore | None = None,
        session_ttl: float = 15 * 60.0,
        stale_after: float = 90.0,
        state_path: str = ".zdx/node_state.json",
        tls_config: TLSConfig | None = None,
        allow_insecure: bool = False,
    ):
        self.host = host
        self.port = port
        self.running = False
        self.credentials = credentials or NodeCredentials(
            NodeIdentity.load_or_create(".zdx/coordinator_key.pem")
        )
        state_base = Path(state_path)
        self.trust = trust or TrustedKeyStore(state_base.with_name("trusted_peers.json"))
        self.sessions = SessionRegistry(session_ttl, state_base.with_name("sessions.json"))
        self.authenticator = MessageAuthenticator(self.trust, self.sessions)
        self.handshake = SessionCoordinator(
            self.credentials, self.trust, self.sessions
        )
        self.registry = ZDXNodeRegistry(stale_after, state_base.with_name("node_registry.json"))
        self.scheduler = ZDXScheduler()
        self.peers = {}
        self.capabilities = {}
        self.state = ZDXState(state_path)
        self._server_socket: socket.socket | None = None
        if tls_config is None and not allow_insecure:
            raise ValueError("TLS is required; use allow_insecure only for local tests")
        self.tls_config = tls_config
        self._tls_context = tls_config.server_context() if tls_config else None
        self._tls_lock = threading.RLock()

    def reload_tls(self, tls_config: TLSConfig) -> None:
        """Atomically replace the TLS context for new connections."""
        context = tls_config.server_context()
        with self._tls_lock:
            self.tls_config = tls_config
            self._tls_context = context

    def enroll(self, node_id: str, public_key: bytes, key_id: str = "1") -> None:
        """Pin an approved node identity before it is allowed to connect."""
        self.trust.enroll(node_id, public_key, key_id)

    def _response(self, kind: str, payload: dict,
                  request: ZDXMessage) -> ZDXMessage:
        return self.credentials.message(
            kind,
            payload,
            session_id=request.session_id,
            sequence=self.sessions.next_sequence(request.session_id),
            request_id=request.request_id,
        )

    def select_worker(self):
        """Return the strongest currently connected worker."""
        return self.scheduler.select_node()

    def handle_client(self, conn, address):
        session_id = ""
        try:
            hello = recv_message(conn)
            challenge = self.handshake.accept_hello(hello)
            send_message(conn, challenge)
            confirmation = recv_message(conn)
            acknowledgement = self.handshake.accept_confirmation(confirmation)
            session_id = acknowledgement.session_id
            send_message(conn, acknowledgement)

            while self.running:
                message = recv_message(conn)
                session = self.authenticator.validate(message)
                if session.session_id != session_id:
                    raise ProtocolError("session", "connection session changed")
                node_id = message.sender_id
                if message.kind == "identity":
                    claimed = message.payload.get("node_id")
                    if claimed != node_id:
                        raise ProtocolError("identity", "payload identity mismatch")
                    capabilities = message.payload.get("capabilities", {})
                    record = self.registry.register(
                        node_id, capabilities, session_id, message.key_id
                    )
                    self.peers[node_id] = address
                    self.capabilities[node_id] = dict(capabilities)
                    self.scheduler.register_node(node_id, capabilities)
                    self.state.record_peer(node_id, {
                        "address": str(address),
                        "key_id": message.key_id,
                    })
                    response = self._response(
                        "identity_ack",
                        {"accepted": True, "reconnects": record["reconnects"]},
                        message,
                    )
                elif message.kind == "capability_report":
                    existing = self.registry.get(node_id)
                    if existing is None:
                        raise ProtocolError("identity", "node is not registered")
                    self.registry.register(
                        node_id, message.payload, session_id, message.key_id
                    )
                    self.capabilities[node_id] = dict(message.payload)
                    self.scheduler.register_node(node_id, message.payload)
                    response = self._response(
                        "capability_ack", {"accepted": True}, message
                    )
                elif message.kind == "heartbeat":
                    if not self.registry.heartbeat(node_id, session_id):
                        raise ProtocolError("identity", "node is not registered")
                    self.state.record_heartbeat()
                    response = self._response(
                        "heartbeat_ack", {"status": "alive"}, message
                    )
                else:
                    response = self._response(
                        "ack", {"received": message.kind}, message
                    )
                send_message(conn, response)
                self.registry.remove_stale()
                self.sessions.cleanup()
        except (ConnectionError, OSError, ProtocolError):
            pass
        finally:
            if session_id:
                try:
                    peer_id = self.sessions.get(session_id).peer_id
                except ProtocolError:
                    peer_id = None
                self.registry.disconnect(session_id)
                self.sessions.close(session_id)
                if peer_id:
                    self.scheduler.remove_node(peer_id)
            conn.close()

    def serve(self):
        self.running = True
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            self._server_socket = server
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.host, self.port))
            self.port = server.getsockname()[1]
            server.listen()
            server.settimeout(0.2)
            while self.running:
                try:
                    conn, address = server.accept()
                except socket.timeout:
                    continue
                if self._tls_context is not None:
                    try:
                        with self._tls_lock:
                            context = self._tls_context
                        conn = context.wrap_socket(conn, server_side=True)
                    except (ssl.SSLError, OSError):
                        conn.close()
                        continue
                thread = threading.Thread(
                    target=self.handle_client, args=(conn, address), daemon=True
                )
                thread.start()
        self._server_socket = None

    def stop(self):
        self.running = False
