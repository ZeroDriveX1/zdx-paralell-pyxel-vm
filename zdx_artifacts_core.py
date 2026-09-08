"""Private content-addressed artifact storage for ZDX workers.

Artifacts are addressed by the SHA-256 of their plaintext contents.  The
storage path is never derived from a network-supplied filesystem path and the
on-disk representation is encrypted with AES-GCM.  Network callers can only
read a blob after a task-scoped grant has been issued by the coordinator.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError as exc:  # pragma: no cover - dependency is pinned for releases
    AESGCM = None
    _CRYPTOGRAPHY_ERROR = exc


MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
TOKEN_BYTES = 32


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _valid_digest(value: str) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        char in "0123456789abcdef" for char in value.lower()
    )


@dataclass(frozen=True)
class ArtifactRef:
    digest: str
    size: int

    def to_dict(self) -> dict:
        return {"digest": self.digest, "size": self.size}


class ContentAddressedBlobStore:
    """Encrypted, private blob store with task/peer scoped reads."""

    def __init__(
        self,
        root: str = ".zdx/artifacts",
        *,
        key: Optional[bytes] = None,
        key_path: Optional[str] = None,
        max_artifact_bytes: int = MAX_ARTIFACT_BYTES,
    ):
        if AESGCM is None:  # pragma: no cover
            raise ImportError("cryptography is required for artifact storage") from _CRYPTOGRAPHY_ERROR
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        self.max_artifact_bytes = max(1, int(max_artifact_bytes))
        self._lock = threading.RLock()
        self._bindings_path = self.root / "bindings.json"
        self._bindings = self._load_bindings()
        self._key = self._load_key(key, key_path)

    def _load_key(self, key: Optional[bytes], key_path: Optional[str]) -> bytes:
        if key is not None:
            if len(key) not in (16, 24, 32):
                raise ValueError("artifact key must be 128, 192, or 256 bits")
            return bytes(key)
        path = Path(key_path) if key_path else self.root / ".key"
        if path.exists():
            value = path.read_bytes()
            if len(value) != 32:
                raise ValueError("artifact key file must contain exactly 32 bytes")
            return value
        value = secrets.token_bytes(32)
        path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(path, flags, 0o600)
        try:
            os.write(fd, value)
        finally:
            os.close(fd)
        return value

    def _load_bindings(self) -> dict:
        if not self._bindings_path.exists():
            return {"version": 1, "tasks": {}, "grants": {}}
        data = json.loads(self._bindings_path.read_text(encoding="utf-8"))
        if data.get("version") != 1 or not isinstance(data.get("tasks"), dict):
            raise ValueError("invalid artifact bindings state")
        data.setdefault("grants", {})
        return data

    def _save_bindings(self) -> None:
        self._bindings_path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".bindings.", dir=str(self.root))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(self._bindings, stream, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self._bindings_path)
            self._bindings_path.chmod(0o600)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _blob_path(self, digest: str) -> Path:
        if not _valid_digest(digest):
            raise ValueError("artifact digest must be a SHA-256 hex digest")
        folder = self.root / digest[:2]
        folder.mkdir(mode=0o700, exist_ok=True)
        return folder / digest[2:]

    def put_bytes(self, data: bytes, *, expected_digest: Optional[str] = None) -> ArtifactRef:
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("artifact content must be bytes")
        if len(data) > self.max_artifact_bytes:
            raise ValueError("artifact exceeds maximum size")
        raw = bytes(data)
        digest = _digest(raw)
        if expected_digest is not None and digest != expected_digest.lower():
            raise ValueError("artifact digest mismatch")
        path = self._blob_path(digest)
        with self._lock:
            if not path.exists():
                nonce = secrets.token_bytes(12)
                encrypted = nonce + AESGCM(self._key).encrypt(nonce, raw, digest.encode("ascii"))
                fd, name = tempfile.mkstemp(prefix=".blob.", dir=str(path.parent))
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(encrypted)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(name, path)
                    path.chmod(0o600)
                finally:
                    if os.path.exists(name):
                        os.unlink(name)
        return ArtifactRef(digest, len(raw))

    def put_file(self, path: str) -> ArtifactRef:
        file_path = Path(path)
        if not file_path.is_file():
            raise FileNotFoundError(path)
        data = file_path.read_bytes()
        return self.put_bytes(data)

    def get_bytes(self, digest: str) -> bytes:
        path = self._blob_path(digest)
        if not path.is_file():
            raise FileNotFoundError(digest)
        encrypted = path.read_bytes()
        if len(encrypted) < 13:
            raise ValueError("artifact blob is truncated")
        raw = AESGCM(self._key).decrypt(encrypted[:12], encrypted[12:], digest.encode("ascii"))
        if _digest(raw) != digest.lower():
            raise ValueError("artifact integrity check failed")
        if len(raw) > self.max_artifact_bytes:
            raise ValueError("artifact exceeds maximum size")
        return raw

    def bind_task(self, task_id: str, digest: str, owner_id: str) -> None:
        if not task_id or not owner_id or not _valid_digest(digest):
            raise ValueError("task, digest, and owner are required")
        with self._lock:
            if not self._blob_path(digest).is_file():
                raise FileNotFoundError(digest)
            self._bindings["tasks"][task_id] = {"digest": digest.lower(), "owner_id": owner_id}
            self._save_bindings()

    def grant(self, task_id: str, digest: str, peer_id: str, ttl_seconds: int = 900) -> str:
        with self._lock:
            binding = self._bindings["tasks"].get(task_id)
            if not binding or binding["digest"] != digest.lower():
                raise PermissionError("artifact is not bound to task")
            token = secrets.token_urlsafe(TOKEN_BYTES)
            token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
            self._bindings["grants"][token_hash] = {
                "task_id": task_id,
                "digest": digest.lower(),
                "peer_id": peer_id,
                "expires_at": time.time() + max(1, int(ttl_seconds)),
            }
            self._save_bindings()
            return token

    def read_granted(self, task_id: str, digest: str, peer_id: str, token: str) -> bytes:
        token_hash = hashlib.sha256(str(token).encode("utf-8")).hexdigest()
        with self._lock:
            grant = self._bindings["grants"].get(token_hash)
            if not grant or grant["task_id"] != task_id or grant["digest"] != digest.lower():
                raise PermissionError("invalid artifact grant")
            if grant["peer_id"] != peer_id or grant["expires_at"] < time.time():
                raise PermissionError("artifact grant expired or belongs to another peer")
        return self.get_bytes(digest)

    def status(self) -> dict:
        with self._lock:
            return {
                "root": str(self.root),
                "tasks": len(self._bindings["tasks"]),
                "active_grants": sum(
                    1 for item in self._bindings["grants"].values() if item["expires_at"] >= time.time()
                ),
            }
