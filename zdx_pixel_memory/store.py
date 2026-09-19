"""Transactional, process-safe PNG-backed pixel memory."""

import hashlib
import io
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from zdx_storage import CorruptStateError, FileLock, StateStore, atomic_write_bytes
from . import codec


class PixelStore:
    _SAFE_KEY = re.compile(r"[^\w\-.]")
    _INDEX_FILE = "keys.json"

    def __init__(self, store_dir: str = "zdx_memory/"):
        self.store_dir = str(store_dir)
        Path(store_dir).mkdir(parents=True, exist_ok=True)
        self._directory_lock = Path(store_dir) / ".pixel-store.lock"
        self._index_store = StateStore(
            Path(store_dir) / self._INDEX_FILE, "pixel-memory-index"
        )
        self._index = self._index_store.load({})

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _canonical(value):
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()

    def _wrap(self, value, created_at=None):
        now = self._now()
        metadata = {
            "schema_version": 1,
            "created_at": created_at or now,
            "updated_at": now,
            "compatibility": {
                "format": "zdx-pixel-memory",
                "min_reader_version": 1,
            },
        }
        metadata["checksum"] = hashlib.sha256(
            self._canonical({"metadata": metadata, "data": value})
        ).hexdigest()
        return {"__zdx_persistent__": {"metadata": metadata, "data": value}}

    def _unwrap(self, document):
        if not (
            isinstance(document, dict)
            and set(document) == {"__zdx_persistent__"}
        ):
            return document  # version-zero PNG remains readable
        envelope = document["__zdx_persistent__"]
        if not isinstance(envelope, dict):
            raise CorruptStateError("pixel-memory envelope must be an object")
        metadata = envelope.get("metadata", {})
        if not isinstance(metadata, dict) or "data" not in envelope:
            raise CorruptStateError("pixel-memory metadata is malformed")
        claimed = metadata.get("checksum", "")
        unsigned = dict(metadata)
        unsigned.pop("checksum", None)
        actual = hashlib.sha256(self._canonical({
            "metadata": unsigned, "data": envelope.get("data")
        })).hexdigest()
        if not secrets.compare_digest(str(claimed), actual):
            raise CorruptStateError("pixel-memory checksum mismatch")
        return envelope.get("data")

    def _index_path(self):
        return os.path.join(self.store_dir, self._INDEX_FILE)

    def _load_index(self):
        return self._index_store.load({})

    def _save_index(self):
        self._index_store.save(self._index)

    def _make_filename(self, key: str):
        safe = self._SAFE_KEY.sub("_", key)
        suffix = hashlib.md5(key.encode()).hexdigest()[:8]
        return f"{safe}_{suffix}.px.png"

    def _old_filename(self, key: str):
        return f"{self._SAFE_KEY.sub('_', key)}.px.png"

    def _old_path(self, key: str):
        return os.path.join(self.store_dir, self._old_filename(key))

    def _read_path(self, path: Path):
        try:
            with Image.open(path) as image:
                return self._unwrap(codec.decode(image))
        except Exception as primary:
            quarantine = Path(str(path) + f".corrupt.{int(time.time())}")
            os.replace(path, quarantine)
            backup = Path(str(path) + ".bak")
            if backup.exists():
                with Image.open(backup) as image:
                    value = self._unwrap(codec.decode(image))
                atomic_write_bytes(path, backup.read_bytes())
                return value
            raise CorruptStateError(
                f"corrupt pixel state quarantined at {quarantine}"
            ) from primary

    def write(self, key: str, value):
        with FileLock(self._directory_lock):
            self._index = self._index_store.load({})
            filename = self._index.get(key, self._make_filename(key))
            path = Path(self.store_dir) / filename
            image = codec.encode(self._wrap(value))
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            atomic_write_bytes(path, buffer.getvalue())
            if key not in self._index:
                self._index[key] = filename
                self._save_index()
            return str(path)

    def read(self, key: str, default=None):
        with FileLock(self._directory_lock):
            self._index = self._index_store.load({})
            if key in self._index:
                path = Path(self.store_dir) / self._index[key]
                if path.exists():
                    return self._read_path(path)
            old = Path(self._old_path(key))
            if old.exists():
                return self._read_path(old)
            return default

    def delete(self, key: str):
        with FileLock(self._directory_lock):
            self._index = self._index_store.load({})
            removed = False
            filename = self._index.pop(key, None)
            if filename:
                self._save_index()  # make deletion visible before removing orphan
                path = Path(self.store_dir) / filename
                if path.exists():
                    path.unlink()
                    removed = True
            old = Path(self._old_path(key))
            if old.exists():
                old.unlink()
                removed = True
            return removed

    def exists(self, key: str):
        sentinel = object()
        return self.read(key, sentinel) is not sentinel

    def keys(self):
        lock = FileLock(self._directory_lock)
        with lock.shared():
            self._index = self._index_store.load({})
            return sorted(self._index)

    def all(self):
        return {key: self.read(key) for key in self.keys()}
