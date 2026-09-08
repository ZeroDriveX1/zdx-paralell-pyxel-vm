"""Resumable chunked transfer layer for the encrypted artifact store."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
import time
from pathlib import Path

from zdx_artifacts_core import ArtifactRef, ContentAddressedBlobStore as _CoreStore
from zdx_artifacts_core import MAX_ARTIFACT_BYTES


MAX_CHUNK_BYTES = 1024 * 1024


class ContentAddressedBlobStore(_CoreStore):
    """Core encrypted store with durable upload/download sessions."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.transfers = self.root / "transfers"
        self.transfers.mkdir(mode=0o700, exist_ok=True)

    def _session_paths(self, transfer_id: str) -> tuple[Path, Path]:
        if not transfer_id or len(transfer_id) > 96 or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in transfer_id):
            raise ValueError("invalid transfer id")
        return self.transfers / f"{transfer_id}.json", self.transfers / f"{transfer_id}.part"

    def begin_upload(self, task_id: str, digest: str, size: int, owner_id: str, ttl_seconds: int = 900) -> dict:
        if not task_id or not owner_id or not isinstance(size, int) or size < 1 or size > self.max_artifact_bytes:
            raise ValueError("invalid artifact upload metadata")
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest.lower()):
            raise ValueError("invalid artifact digest")
        transfer_id = secrets.token_urlsafe(18)
        metadata_path, part_path = self._session_paths(transfer_id)
        with part_path.open("wb") as stream:
            stream.truncate(size)
        part_path.chmod(0o600)
        metadata = {
            "version": 1, "direction": "upload", "transfer_id": transfer_id,
            "task_id": task_id, "digest": digest.lower(), "size": size,
            "owner_id": owner_id, "expires_at": time.time() + max(1, int(ttl_seconds)), "chunks": {},
        }
        self._write_session(metadata_path, metadata)
        return {"transfer_id": transfer_id, "digest": digest.lower(), "size": size, "expires_at": metadata["expires_at"]}

    def _write_session(self, path: Path, data: dict) -> None:
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, path)
            path.chmod(0o600)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _load_session(self, transfer_id: str) -> tuple[dict, Path, Path]:
        metadata_path, part_path = self._session_paths(transfer_id)
        if not metadata_path.exists() or not part_path.exists():
            raise FileNotFoundError("transfer session not found")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("version") != 1 or metadata.get("transfer_id") != transfer_id:
            raise ValueError("invalid transfer session")
        if metadata["expires_at"] < time.time():
            self._discard_session(transfer_id)
            raise PermissionError("transfer session expired")
        return metadata, metadata_path, part_path

    def put_chunk(self, transfer_id: str, peer_id: str, offset: int, data: bytes, chunk_sha256: str) -> dict:
        metadata, metadata_path, part_path = self._load_session(transfer_id)
        if metadata["owner_id"] != peer_id:
            raise PermissionError("transfer belongs to another peer")
        if not isinstance(offset, int) or offset < 0 or len(data) < 1 or len(data) > MAX_CHUNK_BYTES:
            raise ValueError("invalid artifact chunk")
        if offset + len(data) > metadata["size"]:
            raise ValueError("artifact chunk exceeds declared size")
        actual = hashlib.sha256(data).hexdigest()
        if actual != str(chunk_sha256).lower():
            raise ValueError("artifact chunk digest mismatch")
        with part_path.open("r+b") as stream:
            stream.seek(offset)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        metadata["chunks"][str(offset)] = {"size": len(data), "sha256": actual}
        self._write_session(metadata_path, metadata)
        received = sum(item["size"] for item in metadata["chunks"].values())
        return {"transfer_id": transfer_id, "offset": offset, "received_bytes": received, "size": metadata["size"]}

    def finish_upload(self, transfer_id: str, peer_id: str) -> ArtifactRef:
        metadata, _metadata_path, part_path = self._load_session(transfer_id)
        if metadata["owner_id"] != peer_id:
            raise PermissionError("transfer belongs to another peer")
        expected_offset = 0
        for raw_offset, item in sorted(metadata["chunks"].items(), key=lambda pair: int(pair[0])):
            offset = int(raw_offset)
            if offset != expected_offset or offset + item["size"] > metadata["size"]:
                raise ValueError("artifact transfer is incomplete or has gaps")
            expected_offset += item["size"]
        if expected_offset != metadata["size"]:
            raise ValueError("artifact transfer is incomplete")
        data = part_path.read_bytes()
        if hashlib.sha256(data).hexdigest() != metadata["digest"]:
            self._discard_session(transfer_id)
            raise ValueError("artifact final digest mismatch; transfer discarded")
        ref = self.put_bytes(data, expected_digest=metadata["digest"])
        self.bind_task(metadata["task_id"], metadata["digest"], peer_id)
        self._discard_session(transfer_id)
        return ref

    def begin_download(self, task_id: str, digest: str, peer_id: str, ttl_seconds: int = 900) -> dict:
        data = self.get_bytes(digest)
        token = self.grant(task_id, digest, peer_id, ttl_seconds=ttl_seconds)
        return {"task_id": task_id, "digest": digest.lower(), "size": len(data), "chunk_size": MAX_CHUNK_BYTES, "token": token}

    def read_chunk(self, task_id: str, digest: str, peer_id: str, token: str, offset: int) -> dict:
        data = self.read_granted(task_id, digest, peer_id, token)
        if not isinstance(offset, int) or offset < 0 or offset > len(data):
            raise ValueError("invalid artifact download offset")
        chunk = data[offset:offset + MAX_CHUNK_BYTES]
        return {"task_id": task_id, "digest": digest.lower(), "offset": offset, "data": chunk, "done": offset + len(chunk) >= len(data), "size": len(data)}

    def _discard_session(self, transfer_id: str) -> None:
        metadata_path, part_path = self._session_paths(transfer_id)
        for path in (metadata_path, part_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def cleanup_expired(self) -> int:
        removed = 0
        for metadata_path in self.transfers.glob("*.json"):
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                if metadata.get("expires_at", 0) < time.time():
                    self._discard_session(metadata["transfer_id"])
                    removed += 1
            except (OSError, ValueError, KeyError, TypeError):
                self._discard_session(metadata_path.stem)
        with self._lock:
            expired = [key for key, item in self._bindings.get("grants", {}).items() if item.get("expires_at", 0) < time.time()]
            for key in expired:
                self._bindings["grants"].pop(key, None)
            if expired:
                self._save_bindings()
        return removed
