"""Mutually authenticated session establishment for the canonical protocol."""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass

from security.identity import NodeIdentity
from zdx_network import (
    MessageAuthenticator,
    ProtocolError,
    SessionRegistry,
    TrustedKeyStore,
    ZDXMessage,
    payload_checksum,
)


@dataclass(frozen=True)
class NodeCredentials:
    """Canonical network identity and its active key version."""

    identity: NodeIdentity
    key_id: str = "1"

    @property
    def node_id(self) -> str:
        return self.identity.node_id

    @property
    def public_key_bytes(self) -> bytes:
        return self.identity.public_key_bytes

    def message(self, kind: str, payload: dict, **fields) -> ZDXMessage:
        return ZDXMessage(
            kind=kind,
            payload=payload,
            sender_id=self.node_id,
            key_id=self.key_id,
            **fields,
        ).sign(self.identity.private_key.sign)


class SessionCoordinator:
    """Server-side challenge/response session state.

    Enrollment is deliberately separate: a hello is accepted only when its
    identity and key are already pinned in ``TrustedKeyStore``.
    """

    def __init__(self, credentials: NodeCredentials, trust: TrustedKeyStore,
                 sessions: SessionRegistry):
        self.credentials = credentials
        self.trust = trust
        self.sessions = sessions
        self.authenticator = MessageAuthenticator(trust, sessions)
        self._challenges: dict[str, tuple[str, str, float]] = {}
        self._lock = threading.RLock()

    def accept_hello(self, hello: ZDXMessage) -> ZDXMessage:
        if hello.kind != "session_hello":
            raise ProtocolError("authentication", "expected session_hello")
        self.authenticator.validate(hello, require_session=False)
        client_challenge = hello.payload.get("challenge")
        if not isinstance(client_challenge, str) or len(client_challenge) < 32:
            raise ProtocolError("authentication", "invalid client challenge")
        session = self.sessions.establish(
            hello.sender_id, hello.key_id, active=False
        )
        server_challenge = secrets.token_urlsafe(32)
        with self._lock:
            self._challenges[session.session_id] = (
                hello.sender_id, server_challenge, session.expires_at
            )
        return self.credentials.message(
            "session_challenge",
            {
                "client_challenge": client_challenge,
                "server_challenge": server_challenge,
                "expires_at": session.expires_at,
            },
            session_id=session.session_id,
            sequence=0,
        )

    def accept_confirmation(self, confirmation: ZDXMessage) -> ZDXMessage:
        if confirmation.kind != "session_confirm":
            raise ProtocolError("authentication", "expected session_confirm")
        self.authenticator._validate_structure(confirmation)
        if abs(time.time() - confirmation.timestamp) > self.authenticator.max_age:
            raise ProtocolError("timestamp", "confirmation timestamp expired")
        if payload_checksum(confirmation.payload) != confirmation.checksum:
            raise ProtocolError("checksum", "confirmation checksum mismatch")
        self.authenticator.verify_signature(confirmation)
        with self._lock:
            pending = self._challenges.get(confirmation.session_id)
        if pending is None:
            raise ProtocolError("session", "unknown handshake")
        peer_id, challenge, expires_at = pending
        if peer_id != confirmation.sender_id:
            raise ProtocolError("identity", "handshake identity changed")
        if expires_at <= time.time():
            with self._lock:
                self._challenges.pop(confirmation.session_id, None)
            self.sessions.close(confirmation.session_id)
            raise ProtocolError("session", "handshake expired")
        if confirmation.sequence != 0:
            raise ProtocolError("sequence", "confirmation sequence must be zero")
        if confirmation.payload.get("server_challenge") != challenge:
            raise ProtocolError("authentication", "challenge response mismatch")
        self.sessions.activate(confirmation.session_id, confirmation.sender_id)
        with self._lock:
            self._challenges.pop(confirmation.session_id, None)
        return self.credentials.message(
            "session_ack",
            {"authenticated": True, "expires_at": expires_at},
            session_id=confirmation.session_id,
            sequence=0,
        )


class SessionClient:
    """Client-side mutual authentication state and coordinator validation."""

    def __init__(self, credentials: NodeCredentials, trust: TrustedKeyStore):
        self.credentials = credentials
        self.trust = trust
        self.challenge = ""
        self.coordinator_id = ""
        self.session_id = ""
        self.expires_at = 0.0
        self.sequence = 0
        self.response_sequence = 0
        self.response_nonces: set[str] = set()

    def hello(self) -> ZDXMessage:
        self.challenge = secrets.token_urlsafe(32)
        return self.credentials.message(
            "session_hello", {"challenge": self.challenge}
        )

    def confirm(self, challenge: ZDXMessage,
                coordinator_id: str) -> ZDXMessage:
        if challenge.kind != "session_challenge":
            raise ProtocolError("authentication", "expected session_challenge")
        if challenge.sender_id != coordinator_id:
            raise ProtocolError("identity", "unexpected coordinator identity")
        self._verify_coordinator_message(challenge)
        if challenge.payload.get("client_challenge") != self.challenge:
            raise ProtocolError("authentication", "coordinator did not prove hello")
        server_challenge = challenge.payload.get("server_challenge")
        if not isinstance(server_challenge, str) or len(server_challenge) < 32:
            raise ProtocolError("authentication", "invalid coordinator challenge")
        self.coordinator_id = coordinator_id
        self.session_id = challenge.session_id
        self.expires_at = float(challenge.payload["expires_at"])
        return self.credentials.message(
            "session_confirm",
            {"server_challenge": server_challenge},
            session_id=self.session_id,
            sequence=0,
        )

    def accept_ack(self, ack: ZDXMessage) -> None:
        if ack.kind != "session_ack" or ack.session_id != self.session_id:
            raise ProtocolError("authentication", "invalid session acknowledgement")
        if ack.sender_id != self.coordinator_id:
            raise ProtocolError("identity", "acknowledgement sender changed")
        self._verify_coordinator_message(ack)
        if not ack.payload.get("authenticated"):
            raise ProtocolError("authentication", "session was not authenticated")

    def message(self, kind: str, payload: dict) -> ZDXMessage:
        if not self.session_id or self.expires_at <= time.time():
            raise ProtocolError("session", "no active session")
        self.sequence += 1
        return self.credentials.message(
            kind, payload, session_id=self.session_id, sequence=self.sequence
        )

    def validate_response(self, message: ZDXMessage) -> ZDXMessage:
        """Authenticate a coordinator application response and prevent replay."""
        self._verify_coordinator_message(message)
        if message.sender_id != self.coordinator_id:
            raise ProtocolError("identity", "response sender changed")
        if message.session_id != self.session_id:
            raise ProtocolError("session", "response session changed")
        if message.nonce in self.response_nonces:
            raise ProtocolError("nonce", "coordinator nonce already used")
        expected = self.response_sequence + 1
        if message.sequence != expected:
            raise ProtocolError(
                "sequence", f"expected coordinator sequence {expected}"
            )
        self.response_nonces.add(message.nonce)
        self.response_sequence = message.sequence
        return message

    def _verify_coordinator_message(self, message: ZDXMessage) -> None:
        verifier = MessageAuthenticator(self.trust, SessionRegistry())
        verifier._validate_structure(message)
        if abs(time.time() - message.timestamp) > verifier.max_age:
            raise ProtocolError("timestamp", "coordinator message expired")
        if payload_checksum(message.payload) != message.checksum:
            raise ProtocolError("checksum", "coordinator checksum mismatch")
        verifier.verify_signature(message)
