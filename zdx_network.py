"""Canonical authenticated ZDX protocol and length-prefixed transport."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import secrets
import socket
import struct
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Callable, ClassVar, Mapping, Optional

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
except ImportError:
    serialization = None
    Ed25519PublicKey = None
from zdx_storage import StateStore
from zdx_metrics import METRICS

PROTOCOL_VERSION = 1
MAX_MESSAGE_SIZE = 16 * 1024 * 1024
MAX_MESSAGE_AGE = 60.0
DEFAULT_SESSION_TTL = 15 * 60.0
NONCE_BYTES = 24

MESSAGE_TYPES = frozenset({
    "session_hello", "session_challenge", "session_confirm", "session_ack",
    "heartbeat", "heartbeat_ack", "identity", "identity_ack",
    "capability_report", "capability_ack", "frame", "frame_manifest",
    "ack", "error", "auth_error", "test",
    "compute_register", "compute_submit", "compute_poll", "compute_task",
    "compute_result", "compute_release", "compute_fail", "compute_ack",
    "artifact_put", "artifact_get", "artifact_ack", "artifact_data",
    "artifact_upload_begin", "artifact_upload_chunk", "artifact_upload_finish",
    "artifact_upload_ready", "artifact_upload_receipt", "artifact_upload_complete",
    "artifact_download_begin", "artifact_download_chunk",
    "artifact_download_ready", "artifact_transfer_cleanup",
    "artifact_transfer_ack", "cluster_state_push", "cluster_state_pull",
    "cluster_state_ack", "cluster_state_snapshot", "cluster_hello",
})
HANDSHAKE_TYPES = frozenset({
    "session_hello", "session_challenge", "session_confirm", "session_ack",
})


class ProtocolError(ValueError):
    """An inbound packet failed canonical protocol validation."""

    def __init__(self, stage: str, reason: str):
        super().__init__(f"{stage}: {reason}")
        self.stage = stage
        self.reason = reason


def canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProtocolError("encoding", "message is not canonical JSON") from exc


def payload_checksum(payload: Mapping) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def public_key_node_id(public_key_bytes: bytes) -> str:
    return hashlib.sha256(public_key_bytes).hexdigest()


@dataclass
class ZDXMessage:
    """Message envelope compatible with legacy and mutual-session transports."""

    kind: str
    payload: dict
    sender_id: str = ""
    peer_id: str = ""
    session_id: str = ""
    sequence: Optional[int] = 0
    nonce: str = ""
    request_id: str = ""
    version: int = PROTOCOL_VERSION
    timestamp: float = 0.0
    checksum: str = ""
    key_id: str = "1"
    signature: str = ""

    SIGNED_FIELDS: ClassVar[tuple[str, ...]] = (
        "version", "kind", "sender_id", "session_id", "sequence", "timestamp",
        "nonce", "request_id", "key_id", "checksum", "payload",
    )
    LEGACY_FIELDS: ClassVar[tuple[str, ...]] = (
        "kind", "payload", "request_id", "version", "timestamp",
        "peer_id", "signature", "sequence",
    )

    def prepare(self) -> "ZDXMessage":
        if self.sender_id and not self.peer_id:
            self.peer_id = self.sender_id
        elif self.peer_id and not self.sender_id:
            self.sender_id = self.peer_id
        if not self.request_id:
            self.request_id = str(uuid.uuid4())
        if not self.timestamp:
            self.timestamp = time.time()
        if not self.nonce:
            self.nonce = secrets.token_urlsafe(NONCE_BYTES)
        if not self.checksum:
            self.checksum = payload_checksum(self.payload)
        return self

    def _prepare(self) -> None:
        self.prepare()

    def auth_payload(self) -> dict:
        return {name: getattr(self, name) for name in self.LEGACY_FIELDS if name != "signature"}

    def signed_data(self) -> dict:
        return {name: getattr(self, name) for name in self.SIGNED_FIELDS}

    def sign(self, signer: Any) -> "ZDXMessage":
        self.prepare()
        if hasattr(signer, "sign_message"):
            self.signature = signer.sign_message(self.auth_payload())
        else:
            self.signature = base64.b64encode(
                signer(canonical_json(self.signed_data()))
            ).decode("ascii")
        return self

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
        if not isinstance(self.peer_id, str) or not isinstance(self.sender_id, str):
            raise ValueError("peer identities must be strings")
        if not isinstance(self.signature, str):
            raise ValueError("signature must be a string")
        if self.sequence is not None and (
            isinstance(self.sequence, bool) or not isinstance(self.sequence, int)
        ):
            raise ValueError("sequence must be an integer or null")

    def encode(self) -> bytes:
        self.prepare()
        self._validate()
        body = canonical_json(asdict(self))
        if len(body) > MAX_MESSAGE_SIZE:
            raise ProtocolError("size", "message exceeds maximum size")
        return struct.pack(">I", len(body)) + body

    @staticmethod
    def decode(body: bytes) -> "ZDXMessage":
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProtocolError("encoding", "invalid JSON packet") from exc
        if not isinstance(data, dict):
            raise ProtocolError("envelope", "packet must be a JSON object")
        allowed = {item.name for item in fields(ZDXMessage)}
        unknown = set(data) - allowed
        if unknown:
            raise ProtocolError(
                "envelope", f"unknown message fields: {sorted(unknown)}"
            )
        if not {"kind", "payload"}.issubset(data):
            missing = sorted({"kind", "payload"} - set(data))
            raise ProtocolError(
                "envelope", f"field mismatch (missing={missing}, extra=[])"
            )
        session_markers = {"sender_id", "session_id", "nonce", "checksum", "key_id"}
        if session_markers.intersection(data):
            required = set(ZDXMessage.SIGNED_FIELDS) | {"signature"}
            missing = sorted(required - set(data))
            if missing:
                raise ProtocolError(
                    "envelope", f"field mismatch (missing={missing}, extra=[])"
                )
        try:
            message = ZDXMessage(**data)
            message._validate()
            return message
        except TypeError as exc:
            raise ProtocolError("envelope", "message fields are malformed") from exc


@dataclass
class AuthenticatedSession:
    session_id: str
    peer_id: str
    key_id: str
    created_at: float
    expires_at: float
    last_sequence: int = 0
    next_outbound_sequence: int = 1
    nonces: dict[str, float] = field(default_factory=dict)
    active: bool = True

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at


class SessionRegistry:
    """Thread-safe session lifecycle, sequence, and nonce state."""

    def __init__(self, ttl: float = DEFAULT_SESSION_TTL, path=None):
        if ttl <= 0:
            raise ValueError("session TTL must be positive")
        self.ttl = ttl
        self._sessions: dict[str, AuthenticatedSession] = {}
        self._peer_session: dict[str, str] = {}
        self._lock = threading.RLock()
        self._store = StateStore(path, "session-metadata") if path else None
        self.recovered_metadata = (
            self._store.load({"sessions": []}).get("sessions", [])
            if self._store else []
        )

    def _persist(self):
        METRICS.gauge("active_sessions", len(self._sessions))
        if self._store:
            self._store.save({"sessions": [
                {
                    "session_id": item.session_id, "peer_id": item.peer_id,
                    "key_id": item.key_id, "created_at": item.created_at,
                    "expires_at": item.expires_at, "last_sequence": item.last_sequence,
                    "next_outbound_sequence": item.next_outbound_sequence,
                    "active_at_shutdown": item.active,
                } for item in self._sessions.values()
            ]})

    def establish(self, peer_id: str, key_id: str = "1",
                  now: Optional[float] = None,
                  active: bool = True) -> AuthenticatedSession:
        current = time.time() if now is None else now
        with self._lock:
            old_id = self._peer_session.get(peer_id)
            if old_id:
                self._sessions.pop(old_id, None)
            session = AuthenticatedSession(
                secrets.token_urlsafe(32), peer_id, key_id, current,
                current + self.ttl,
                active=active,
            )
            self._sessions[session.session_id] = session
            self._peer_session[peer_id] = session.session_id
            self._persist()
            return session

    def get(self, session_id: str, peer_id: Optional[str] = None,
            now: Optional[float] = None,
            require_active: bool = True) -> AuthenticatedSession:
        current = time.time() if now is None else now
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise ProtocolError("session", "unknown session")
            if session.expires_at <= current:
                self.close(session_id)
                raise ProtocolError("session", "session expired")
            if peer_id is not None and session.peer_id != peer_id:
                raise ProtocolError("identity", "session does not belong to sender")
            if require_active and not session.active:
                raise ProtocolError("session", "session handshake is incomplete")
            return session

    def activate(self, session_id: str, peer_id: str) -> AuthenticatedSession:
        with self._lock:
            session = self.get(session_id, peer_id, require_active=False)
            session.active = True
            self._persist()
            return session

    def validate_replay(self, message: ZDXMessage,
                        now: Optional[float] = None) -> AuthenticatedSession:
        current = time.time() if now is None else now
        with self._lock:
            session = self.get(message.session_id, message.sender_id, current)
            cutoff = current - MAX_MESSAGE_AGE
            session.nonces = {
                nonce: stamp for nonce, stamp in session.nonces.items()
                if stamp >= cutoff
            }
            if message.nonce in session.nonces:
                raise ProtocolError("nonce", "nonce already used")
            if message.sequence != session.last_sequence + 1:
                raise ProtocolError(
                    "sequence",
                    f"expected {session.last_sequence + 1}, got {message.sequence}",
                )
            session.nonces[message.nonce] = message.timestamp
            session.last_sequence = message.sequence
            self._persist()
            return session

    def next_sequence(self, session_id: str) -> int:
        with self._lock:
            session = self.get(session_id)
            result = session.next_outbound_sequence
            session.next_outbound_sequence += 1
            self._persist()
            return result

    def close(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(session_id, None)
            if session and self._peer_session.get(session.peer_id) == session_id:
                self._peer_session.pop(session.peer_id, None)
            self._persist()

    def cleanup(self, now: Optional[float] = None) -> int:
        current = time.time() if now is None else now
        with self._lock:
            expired = [
                identifier for identifier, session in self._sessions.items()
                if session.expires_at <= current
            ]
            for identifier in expired:
                self.close(identifier)
            return len(expired)


class TrustedKeyStore:
    """Pinned peer keys with explicit, versioned key rotation."""

    def __init__(self, path=None):
        if Ed25519PublicKey is None:
            raise RuntimeError(
                "session security requires: pip install -r requirements-security.txt"
            )
        self._keys: dict[str, dict[str, Ed25519PublicKey]] = {}
        self._active: dict[str, str] = {}
        self._lock = threading.RLock()
        self._store = StateStore(path, "trusted-peers") if path else None
        if self._store:
            saved = self._store.load({"peers": {}})
            for node_id, record in saved.get("peers", {}).items():
                for key_id, encoded in record.get("keys", {}).items():
                    self._keys.setdefault(node_id, {})[key_id] = Ed25519PublicKey.from_public_bytes(base64.b64decode(encoded))
                if record.get("active") in self._keys.get(node_id, {}):
                    self._active[node_id] = record["active"]

    def _persist(self):
        if self._store:
            self._store.save({"peers": {
                node_id: {"active": self._active.get(node_id), "keys": {
                    key_id: base64.b64encode(self._raw(key)).decode("ascii")
                    for key_id, key in keys.items()
                }} for node_id, keys in self._keys.items()
            }})

    @staticmethod
    def _raw(key: Ed25519PublicKey) -> bytes:
        return key.public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )

    def enroll(self, node_id: str, public_key: bytes, key_id: str = "1") -> None:
        if node_id != public_key_node_id(public_key):
            raise ProtocolError("identity", "node ID does not match public key")
        key = Ed25519PublicKey.from_public_bytes(public_key)
        with self._lock:
            existing = self._keys.get(node_id, {})
            if key_id in existing:
                if self._raw(existing[key_id]) != public_key:
                    raise ProtocolError("identity", "key ID already pinned")
                return
            self._keys.setdefault(node_id, {})[key_id] = key
            self._active[node_id] = key_id
            self._persist()

    def rotate(self, node_id: str, public_key: bytes, new_key_id: str,
               authorize: Callable[[str, str, bytes], bool]) -> None:
        with self._lock:
            old_key_id = self._active.get(node_id)
            if old_key_id is None:
                raise ProtocolError("key_rotation", "identity is not enrolled")
            if new_key_id in self._keys[node_id]:
                raise ProtocolError("key_rotation", "key version already exists")
            if not authorize(node_id, old_key_id, public_key):
                raise ProtocolError("key_rotation", "rotation was not authorized")
            self._keys[node_id][new_key_id] = Ed25519PublicKey.from_public_bytes(
                public_key
            )
            self._active[node_id] = new_key_id
            self._persist()

    def get(self, node_id: str, key_id: str) -> Ed25519PublicKey:
        try:
            return self._keys[node_id][key_id]
        except KeyError as exc:
            raise ProtocolError("identity", "unknown sender or key") from exc

    def is_enrolled(self, node_id: str) -> bool:
        return node_id in self._active


class MessageAuthenticator:
    """Fail-closed validation for every inbound authenticated packet."""

    def __init__(self, trust: TrustedKeyStore, sessions: SessionRegistry,
                 max_age: float = MAX_MESSAGE_AGE):
        self.trust = trust
        self.sessions = sessions
        self.max_age = max_age

    def validate(self, message: ZDXMessage, now: Optional[float] = None,
                 require_session: bool = True) -> AuthenticatedSession | None:
        current = time.time() if now is None else now
        self._validate_structure(message)
        if abs(current - message.timestamp) > self.max_age:
            raise ProtocolError("timestamp", "message timestamp outside allowed window")
        if payload_checksum(message.payload) != message.checksum:
            raise ProtocolError("checksum", "payload checksum mismatch")
        self.verify_signature(message)
        if require_session:
            return self.sessions.validate_replay(message, current)
        if message.session_id or message.sequence != 0:
            raise ProtocolError("session", "handshake packet has invalid session state")
        return None

    def verify_signature(self, message: ZDXMessage) -> None:
        """Verify identity and signature without mutating replay state."""
        public_key = self.trust.get(message.sender_id, message.key_id)
        try:
            signature = base64.b64decode(message.signature, validate=True)
            public_key.verify(signature, canonical_json(message.signed_data()))
        except Exception as exc:
            raise ProtocolError("signature", "signature verification failed") from exc

    @staticmethod
    def _validate_structure(message: ZDXMessage) -> None:
        if message.version != PROTOCOL_VERSION:
            raise ProtocolError("protocol_version", "unsupported protocol version")
        if message.kind not in MESSAGE_TYPES:
            raise ProtocolError("message_type", "unsupported message type")
        if not isinstance(message.payload, dict):
            raise ProtocolError("payload", "payload must be an object")
        if not message.sender_id:
            raise ProtocolError("identity", "sender identity is required")
        if not isinstance(message.sequence, int) or isinstance(message.sequence, bool):
            raise ProtocolError("sequence", "sequence must be an integer")
        if message.sequence < 0:
            raise ProtocolError("sequence", "sequence cannot be negative")
        if not isinstance(message.timestamp, (int, float)) or isinstance(
            message.timestamp, bool
        ) or not math.isfinite(message.timestamp):
            raise ProtocolError("timestamp", "timestamp must be finite")
        if len(message.nonce) < 32:
            raise ProtocolError("nonce", "nonce is missing or too short")
        if not message.signature:
            raise ProtocolError("signature", "signature is required")
        if message.kind not in HANDSHAKE_TYPES and not message.session_id:
            raise ProtocolError("session", "authenticated session is required")


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
    if size == 0 or size > MAX_MESSAGE_SIZE:
        raise ProtocolError("size", "invalid incoming message size")
    return ZDXMessage.decode(recv_exact(sock, size))


def send_message(sock: socket.socket, message: ZDXMessage) -> None:
    sock.sendall(message.encode())


def heartbeat(**kwargs) -> ZDXMessage:
    return ZDXMessage(kind="heartbeat", payload={"status": "alive"}, **kwargs)


def frame_announce(path: str, sha256: str, **kwargs) -> ZDXMessage:
    return ZDXMessage(kind="frame", payload={"path": path, "sha256": sha256}, **kwargs)
