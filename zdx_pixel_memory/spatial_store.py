"""Hardened spatial PNG-backed agent memory.

The store keeps the complete key/value map inside one standards-valid RGB PNG.
Unlike the legacy PixelStore path, the payload is a deterministic binary typed
encoding rather than JSON text.  A SpatialPixelStore may own a pure-memory PNG
or bind to a named storage region inside an executable SpatialFrame.

The PNG is the persistence container; the decoded raster is the address space.
Writes are process-locked and atomically committed with a previous-generation
backup.  Corrupt primary generations are quarantined and recovered from a valid
backup when possible.
"""

from __future__ import annotations

import hashlib
import math
import os
import secrets
import struct
import time
from pathlib import Path
from typing import Mapping

from zdx_spatial_frame import SpatialFrame, SpatialLayout
from zdx_storage import CorruptStateError, FileLock, atomic_write_bytes


_MAGIC = b"ZDXSPM1\x00"
_VERSION = 1
_HEADER = struct.Struct(">8sBBHQI32sII")
_HEADER_SIZE = _HEADER.size

_TAG_NONE = 0
_TAG_FALSE = 1
_TAG_TRUE = 2
_TAG_INT = 3
_TAG_FLOAT = 4
_TAG_STRING = 5
_TAG_BYTES = 6
_TAG_LIST = 7
_TAG_DICT = 8


def _u16(value: int) -> bytes:
    if not 0 <= value <= 0xFFFF:
        raise ValueError("length exceeds uint16")
    return struct.pack(">H", value)


def _u32(value: int) -> bytes:
    if not 0 <= value <= 0xFFFFFFFF:
        raise ValueError("length exceeds uint32")
    return struct.pack(">I", value)


def _read_u16(data: bytes, offset: int) -> tuple[int, int]:
    if offset + 2 > len(data):
        raise ValueError("truncated uint16")
    return struct.unpack_from(">H", data, offset)[0], offset + 2


def _read_u32(data: bytes, offset: int) -> tuple[int, int]:
    if offset + 4 > len(data):
        raise ValueError("truncated uint32")
    return struct.unpack_from(">I", data, offset)[0], offset + 4


def _take(data: bytes, offset: int, length: int) -> tuple[bytes, int]:
    end = offset + length
    if length < 0 or end > len(data):
        raise ValueError("truncated spatial value")
    return data[offset:end], end


def _encode_value(value) -> bytes:
    if value is None:
        return bytes([_TAG_NONE])
    if value is False:
        return bytes([_TAG_FALSE])
    if value is True:
        return bytes([_TAG_TRUE])
    if isinstance(value, int) and not isinstance(value, bool):
        sign = 1 if value < 0 else 0
        magnitude = abs(value)
        raw = b"" if magnitude == 0 else magnitude.to_bytes((magnitude.bit_length() + 7) // 8, "big")
        return bytes([_TAG_INT, sign]) + _u16(len(raw)) + raw
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite floats are not supported")
        return bytes([_TAG_FLOAT]) + struct.pack(">d", value)
    if isinstance(value, str):
        raw = value.encode("utf-8")
        return bytes([_TAG_STRING]) + _u32(len(raw)) + raw
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        return bytes([_TAG_BYTES]) + _u32(len(raw)) + raw
    if isinstance(value, (list, tuple)):
        encoded = [_encode_value(item) for item in value]
        out = bytearray([_TAG_LIST])
        out += _u32(len(encoded))
        for item in encoded:
            out += _u32(len(item))
            out += item
        return bytes(out)
    if isinstance(value, Mapping):
        keys = sorted(value)
        if any(not isinstance(key, str) for key in keys):
            raise TypeError("spatial memory dictionary keys must be strings")
        out = bytearray([_TAG_DICT])
        out += _u32(len(keys))
        for key in keys:
            key_bytes = key.encode("utf-8")
            encoded = _encode_value(value[key])
            out += _u16(len(key_bytes))
            out += key_bytes
            out += _u32(len(encoded))
            out += encoded
        return bytes(out)
    raise TypeError(f"unsupported spatial memory type: {type(value).__name__}")


def encoded_value_size(value) -> int:
    """Return deterministic binary size used by the spatial value codec."""
    return len(_encode_value(value))


def _decode_value(data: bytes, offset: int = 0):
    if offset >= len(data):
        raise ValueError("missing spatial value tag")
    tag = data[offset]
    offset += 1
    if tag == _TAG_NONE:
        return None, offset
    if tag == _TAG_FALSE:
        return False, offset
    if tag == _TAG_TRUE:
        return True, offset
    if tag == _TAG_INT:
        raw, offset = _take(data, offset, 1)
        sign = raw[0]
        if sign not in (0, 1):
            raise ValueError("invalid integer sign")
        length, offset = _read_u16(data, offset)
        raw, offset = _take(data, offset, length)
        value = int.from_bytes(raw, "big") if raw else 0
        return (-value if sign else value), offset
    if tag == _TAG_FLOAT:
        raw, offset = _take(data, offset, 8)
        value = struct.unpack(">d", raw)[0]
        if not math.isfinite(value):
            raise ValueError("non-finite float in spatial memory")
        return value, offset
    if tag in (_TAG_STRING, _TAG_BYTES):
        length, offset = _read_u32(data, offset)
        raw, offset = _take(data, offset, length)
        if tag == _TAG_BYTES:
            return raw, offset
        try:
            return raw.decode("utf-8"), offset
        except UnicodeDecodeError as exc:
            raise ValueError("invalid UTF-8 spatial string") from exc
    if tag == _TAG_LIST:
        count, offset = _read_u32(data, offset)
        items = []
        for _ in range(count):
            length, offset = _read_u32(data, offset)
            raw, offset = _take(data, offset, length)
            item, consumed = _decode_value(raw, 0)
            if consumed != len(raw):
                raise ValueError("trailing bytes in list item")
            items.append(item)
        return items, offset
    if tag == _TAG_DICT:
        count, offset = _read_u32(data, offset)
        result = {}
        previous = None
        for _ in range(count):
            key_length, offset = _read_u16(data, offset)
            key_raw, offset = _take(data, offset, key_length)
            try:
                key = key_raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("invalid UTF-8 spatial key") from exc
            if previous is not None and key <= previous:
                raise ValueError("spatial dictionary keys are not canonical")
            previous = key
            value_length, offset = _read_u32(data, offset)
            raw, offset = _take(data, offset, value_length)
            value, consumed = _decode_value(raw, 0)
            if consumed != len(raw):
                raise ValueError("trailing bytes in dictionary value")
            result[key] = value
        return result, offset
    raise ValueError(f"unknown spatial value tag: {tag}")


class SpatialPixelStore:
    """Deterministic binary key/value store inside a spatial PNG region."""

    def __init__(
        self,
        path: str = "zdx_memory.spatial.png",
        *,
        layout: SpatialLayout | None = None,
        width: int = 256,
        height: int = 256,
        execution_rows: int = 0,
        region: str | None = None,
        lock_timeout: float = 10.0,
    ):
        self.path = str(path)
        self.layout = layout or SpatialLayout(
            width=width,
            height=height,
            execution_rows=execution_rows,
        )
        self.region = region
        self.lock_timeout = lock_timeout
        target = Path(self.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path = Path(str(target) + ".lock")
        if self.capacity_bytes <= _HEADER_SIZE:
            raise ValueError("spatial memory region is too small for its header")
        if not target.exists():
            with self._lock():
                if not target.exists():
                    self._write_unlocked({})

    @property
    def capacity_bytes(self) -> int:
        return self.layout.region(self.region).capacity_bytes

    @property
    def frame_path(self) -> str:
        return self.path

    def _lock(self) -> FileLock:
        return FileLock(self._lock_path, timeout=self.lock_timeout)

    def encoded_document_size(self, values: dict) -> int:
        if not isinstance(values, dict):
            raise TypeError("spatial memory root must be a dictionary")
        return _HEADER_SIZE + encoded_value_size(values)

    def _encode_document(self, values: dict, generation: int) -> bytes:
        payload = _encode_value(values)
        if _HEADER_SIZE + len(payload) > self.capacity_bytes:
            raise ValueError(
                f"spatial memory payload requires {_HEADER_SIZE + len(payload)} bytes; "
                f"region capacity is {self.capacity_bytes}"
            )
        digest = hashlib.sha256(payload).digest()
        header = _HEADER.pack(
            _MAGIC, _VERSION, 0, 0, generation, len(payload), digest, len(values), 0
        )
        return header + payload

    def _decode_document(self, frame: SpatialFrame) -> tuple[dict, int]:
        header = frame.read_bytes(_HEADER_SIZE, region=self.region)
        if header == b"\x00" * _HEADER_SIZE:
            return {}, 0
        try:
            magic, version, flags, reserved, generation, payload_length, digest, count, tail = _HEADER.unpack(header)
        except struct.error as exc:
            raise CorruptStateError("invalid spatial memory header") from exc
        if magic != _MAGIC or version != _VERSION or flags != 0 or reserved != 0 or tail != 0:
            raise CorruptStateError("unsupported or corrupt spatial memory header")
        if payload_length > self.capacity_bytes - _HEADER_SIZE:
            raise CorruptStateError("spatial memory payload length exceeds region")
        payload = frame.read_bytes(
            payload_length, region=self.region, offset=_HEADER_SIZE
        )
        if not secrets.compare_digest(hashlib.sha256(payload).digest(), digest):
            raise CorruptStateError("spatial memory checksum mismatch")
        try:
            values, consumed = _decode_value(payload)
        except (TypeError, ValueError, struct.error) as exc:
            raise CorruptStateError("spatial memory payload is malformed") from exc
        if consumed != len(payload) or not isinstance(values, dict):
            raise CorruptStateError("spatial memory root must be one canonical dictionary")
        if len(values) != count:
            raise CorruptStateError("spatial memory record count mismatch")
        return values, generation

    def _open_frame(self, path: Path | None = None) -> SpatialFrame:
        return SpatialFrame.open(str(path or self.path), self.layout)

    def _execution_plane_matches(self, primary: SpatialFrame, backup: SpatialFrame) -> bool:
        if self.layout.execution_rows == 0:
            return True
        length = self.layout.execution_region.capacity_bytes
        primary_bytes = primary.read_bytes(length, region="execution")
        backup_bytes = backup.read_bytes(length, region="execution")
        return secrets.compare_digest(
            hashlib.sha256(primary_bytes).digest(),
            hashlib.sha256(backup_bytes).digest(),
        )

    def _read_frame_unlocked(self) -> tuple[SpatialFrame, dict, int]:
        target = Path(self.path)
        primary_frame = None
        try:
            primary_frame = self._open_frame()
            values, generation = self._decode_document(primary_frame)
            return primary_frame, values, generation
        except Exception as primary:
            quarantine = Path(str(target) + f".corrupt.{int(time.time())}")
            backup = Path(str(target) + ".bak")
            recovered = None
            if backup.exists():
                try:
                    backup_frame = self._open_frame(backup)
                    values, generation = self._decode_document(backup_frame)
                    if primary_frame is not None and self._execution_plane_matches(primary_frame, backup_frame):
                        recovered = (backup_frame, values, generation)
                    elif self.layout.execution_rows == 0:
                        recovered = (backup_frame, values, generation)
                except Exception:
                    recovered = None
            if target.exists():
                os.replace(target, quarantine)
            if recovered is not None:
                backup_frame, values, generation = recovered
                atomic_write_bytes(
                    target,
                    backup_frame.to_png_bytes(),
                    keep_backup=False,
                    _already_locked=True,
                )
                return backup_frame, values, generation
            reason = (
                "backup execution plane differs from current frame"
                if backup.exists() and self.layout.execution_rows > 0
                else "no valid compatible backup"
            )
            raise CorruptStateError(
                f"corrupt spatial memory quarantined at {quarantine}; {reason}"
            ) from primary

    def _read_unlocked(self) -> tuple[dict, int]:
        _, values, generation = self._read_frame_unlocked()
        return values, generation

    def _write_frame_unlocked(self, frame: SpatialFrame, values: dict, generation: int) -> None:
        document = self._encode_document(values, generation + 1)
        frame.write_bytes(b"\x00" * self.capacity_bytes, region=self.region)
        frame.write_bytes(document, region=self.region)

    def _write_unlocked(self, values: dict, generation: int | None = None) -> None:
        target = Path(self.path)
        if target.exists():
            frame = self._open_frame()
            if generation is None:
                try:
                    _, current_generation = self._decode_document(frame)
                except Exception:
                    current_generation = 0
            else:
                current_generation = generation
        else:
            frame = SpatialFrame.blank(self.layout)
            current_generation = generation or 0
        self._write_frame_unlocked(frame, values, current_generation)
        atomic_write_bytes(target, frame.to_png_bytes(), _already_locked=True)

    def load_resident_snapshot(self) -> tuple[SpatialFrame, dict, int, str]:
        """Load one verified frame/memory generation for resident execution."""
        with self._lock():
            frame, values, generation = self._read_frame_unlocked()
            artifact_sha256 = hashlib.sha256(Path(self.path).read_bytes()).hexdigest()
            return frame, dict(values), generation, artifact_sha256

    def commit_snapshot(
        self,
        frame: SpatialFrame,
        values: dict,
        *,
        vm_generation: int,
        vm_checkpoint_hash: str,
        expected_artifact_sha256: str | None = None,
    ) -> dict:
        """Commit a frozen resident snapshot with optional CAS protection."""
        if not isinstance(values, dict):
            raise TypeError("checkpoint values must be a dictionary")
        with self._lock():
            target = Path(self.path)
            if expected_artifact_sha256 is not None:
                current = hashlib.sha256(target.read_bytes()).hexdigest()
                if not secrets.compare_digest(current, expected_artifact_sha256):
                    raise RuntimeError("spatial checkpoint parent artifact changed concurrently")
            current_generation = 0
            if target.exists():
                try:
                    _, current_generation = self._decode_document(self._open_frame())
                except Exception:
                    current_generation = 0
            self._write_frame_unlocked(frame, values, current_generation)
            payload = frame.to_png_bytes()
            atomic_write_bytes(target, payload, _already_locked=True)
            return {
                "vm_generation": int(vm_generation),
                "vm_checkpoint_hash": str(vm_checkpoint_hash),
                "artifact_sha256": hashlib.sha256(payload).hexdigest(),
            }

    def transaction_frame(self, mutator):
        """Run one locked resident-frame transaction and checkpoint once.

        mutator(frame, values) may execute the resident VM raster and mutate
        the supplied memory dictionary. If it raises, no PNG commit occurs.
        """
        if not callable(mutator):
            raise TypeError("spatial frame transaction requires a callable")
        with self._lock():
            frame, values, generation = self._read_frame_unlocked()
            result = mutator(frame, values)
            if not isinstance(values, dict):
                raise TypeError("spatial frame transaction memory root must remain a dictionary")
            self._write_frame_unlocked(frame, values, generation)
            atomic_write_bytes(self.path, frame.to_png_bytes(), _already_locked=True)
            return result

    def write(self, key: str, value) -> str:
        if not isinstance(key, str) or not key:
            raise ValueError("spatial memory key must be a non-empty string")
        with self._lock():
            values, generation = self._read_unlocked()
            values[key] = value
            self._write_unlocked(values, generation)
        return self.path

    def update(self, data: Mapping) -> str:
        """Commit multiple memory keys in one raster rewrite."""
        if not isinstance(data, Mapping):
            raise TypeError("spatial memory update requires a mapping")
        for key in data:
            if not isinstance(key, str) or not key:
                raise ValueError("spatial memory key must be a non-empty string")
        with self._lock():
            values, generation = self._read_unlocked()
            values.update(data)
            self._write_unlocked(values, generation)
        return self.path

    def read(self, key: str, default=None):
        with self._lock():
            values, _ = self._read_unlocked()
            return values.get(key, default)

    def delete(self, key: str) -> bool:
        with self._lock():
            values, generation = self._read_unlocked()
            if key not in values:
                return False
            del values[key]
            self._write_unlocked(values, generation)
            return True

    def exists(self, key: str) -> bool:
        with self._lock():
            values, _ = self._read_unlocked()
            return key in values

    def keys(self) -> list:
        with self._lock():
            values, _ = self._read_unlocked()
            return sorted(values)

    def all(self) -> dict:
        with self._lock():
            values, _ = self._read_unlocked()
            return dict(values)

    def clear(self) -> None:
        with self._lock():
            _, generation = self._read_unlocked()
            self._write_unlocked({}, generation)

    def generation(self) -> int:
        with self._lock():
            _, generation = self._read_unlocked()
            return generation
