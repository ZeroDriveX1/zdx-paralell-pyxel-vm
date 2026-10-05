"""Bounded hash-chained provenance journal stored in a spatial ABI region."""

from __future__ import annotations

import hashlib
import secrets
import struct
from dataclasses import dataclass

from zdx_spatial_frame import SpatialFrame


_MAGIC = b"ZDXJRN1\x00"
_VERSION = 1
_ZERO_HASH = b"\x00" * 32
_HEADER = struct.Struct(">8sBBHIIQQ32s")
_SLOT_HEADER = struct.Struct(">QQHH32s32s")
_HEADER_SIZE = _HEADER.size
_SLOT_HEADER_SIZE = _SLOT_HEADER.size
_DEFAULT_SLOT_SIZE = 384
_DOMAIN = b"zdx-agent-journal-v1\x00"


@dataclass(frozen=True)
class JournalRecord:
    sequence: int
    generation: int
    event_type: str
    payload: bytes
    previous_sha256: str
    sha256: str


class SpatialEventJournal:
    """Rolling provenance journal with per-record and chain integrity."""

    def __init__(
        self,
        frame: SpatialFrame,
        region: str,
        *,
        slot_size: int = _DEFAULT_SLOT_SIZE,
    ):
        if isinstance(slot_size, bool) or not isinstance(slot_size, int):
            raise ValueError("journal slot_size must be an integer")
        if slot_size <= _SLOT_HEADER_SIZE:
            raise ValueError("journal slot_size is too small")
        self.frame = frame
        self.region = region
        target = frame.layout.region(region)
        if target.y < frame.layout.execution_rows:
            raise ValueError("journal region overlaps execution plane")
        self.capacity_bytes = target.capacity_bytes
        self.slot_size = slot_size
        self.slot_count = (self.capacity_bytes - _HEADER_SIZE) // slot_size
        if self.slot_count < 1:
            raise ValueError("journal region is too small for one slot")
        self._initialized = self._ensure_header()

    @property
    def initialized_new(self) -> bool:
        return self._initialized

    @property
    def payload_capacity(self) -> int:
        return self.slot_size - _SLOT_HEADER_SIZE

    def _read_region(self) -> bytearray:
        return bytearray(self.frame.read_bytes(self.capacity_bytes, region=self.region))

    def _write_region(self, raw: bytearray) -> None:
        if len(raw) != self.capacity_bytes:
            raise ValueError("journal region image has incorrect size")
        self.frame.write_bytes(bytes(raw), region=self.region)

    def _header(self, raw: bytearray) -> tuple[int, int, bytes]:
        magic, version, flags, reserved, slot_size, slot_count, next_seq, count, head = _HEADER.unpack_from(raw, 0)
        if magic != _MAGIC or version != _VERSION or flags != 0 or reserved != 0:
            raise ValueError("corrupt or unsupported spatial journal header")
        if slot_size != self.slot_size or slot_count != self.slot_count:
            raise ValueError("spatial journal geometry does not match existing header")
        if next_seq < 1 or count > slot_count or count > next_seq - 1:
            raise ValueError("corrupt spatial journal sequence state")
        if count == 0 and head != _ZERO_HASH:
            raise ValueError("empty spatial journal has nonzero chain head")
        return next_seq, count, head

    def _write_header(
        self,
        raw: bytearray,
        *,
        next_seq: int,
        count: int,
        head: bytes,
    ) -> None:
        _HEADER.pack_into(
            raw, 0, _MAGIC, _VERSION, 0, 0,
            self.slot_size, self.slot_count, next_seq, count, head,
        )

    def _ensure_header(self) -> bool:
        raw = self._read_region()
        if raw[:_HEADER_SIZE] == b"\x00" * _HEADER_SIZE:
            self._write_header(raw, next_seq=1, count=0, head=_ZERO_HASH)
            self._write_region(raw)
            return True
        self._header(raw)
        return False

    def _slot_offset(self, sequence: int) -> int:
        return _HEADER_SIZE + ((sequence - 1) % self.slot_count) * self.slot_size

    @staticmethod
    def _digest(
        sequence: int,
        generation: int,
        event_type: bytes,
        payload: bytes,
        previous: bytes,
    ) -> bytes:
        h = hashlib.sha256()
        h.update(_DOMAIN)
        h.update(sequence.to_bytes(8, "big"))
        h.update(generation.to_bytes(8, "big"))
        h.update(len(event_type).to_bytes(2, "big"))
        h.update(event_type)
        h.update(len(payload).to_bytes(4, "big"))
        h.update(payload)
        h.update(previous)
        return h.digest()

    def append(
        self,
        event_type: str,
        payload: bytes = b"",
        *,
        generation: int,
    ) -> JournalRecord:
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
            raise ValueError("journal generation must be a non-negative integer")
        event_b = event_type.encode("utf-8")
        payload_b = bytes(payload)
        if not event_b or len(event_b) > 0xFFFF:
            raise ValueError("journal event_type must be non-empty and <= 65535 bytes")
        if len(event_b) + len(payload_b) > self.payload_capacity:
            raise ValueError(
                f"journal event requires {len(event_b) + len(payload_b)} bytes; "
                f"slot payload capacity is {self.payload_capacity}"
            )

        raw = self._read_region()
        next_seq, count, head = self._header(raw)
        sequence = next_seq
        digest = self._digest(sequence, generation, event_b, payload_b, head)
        offset = self._slot_offset(sequence)
        _SLOT_HEADER.pack_into(
            raw,
            offset,
            sequence,
            generation,
            len(event_b),
            len(payload_b),
            head,
            digest,
        )
        body_start = offset + _SLOT_HEADER_SIZE
        raw[body_start : body_start + self.payload_capacity] = b"\x00" * self.payload_capacity
        raw[body_start : body_start + len(event_b)] = event_b
        raw[
            body_start + len(event_b) :
            body_start + len(event_b) + len(payload_b)
        ] = payload_b
        self._write_header(
            raw,
            next_seq=next_seq + 1,
            count=min(self.slot_count, count + 1),
            head=digest,
        )
        self._write_region(raw)
        return JournalRecord(
            sequence=sequence,
            generation=generation,
            event_type=event_type,
            payload=payload_b,
            previous_sha256=head.hex(),
            sha256=digest.hex(),
        )

    def records(self) -> list[JournalRecord]:
        raw = self._read_region()
        next_seq, count, head = self._header(raw)
        if count == 0:
            return []
        start = next_seq - count
        result: list[JournalRecord] = []
        prior_hash = None
        for sequence in range(start, next_seq):
            record = self._decode_slot(raw, sequence)
            if prior_hash is not None and record.previous_sha256 != prior_hash:
                raise ValueError("spatial journal chain linkage mismatch")
            result.append(record)
            prior_hash = record.sha256
        if not secrets.compare_digest(bytes.fromhex(result[-1].sha256), head):
            raise ValueError("spatial journal chain head mismatch")
        return result

    def _decode_slot(self, raw: bytearray, expected_sequence: int) -> JournalRecord:
        offset = self._slot_offset(expected_sequence)
        sequence, generation, event_len, payload_len, previous, digest = _SLOT_HEADER.unpack_from(raw, offset)
        if sequence != expected_sequence:
            raise ValueError("corrupt spatial journal slot sequence")
        total = event_len + payload_len
        if total > self.payload_capacity:
            raise ValueError("corrupt spatial journal slot length")
        body_start = offset + _SLOT_HEADER_SIZE
        event_b = bytes(raw[body_start : body_start + event_len])
        payload_b = bytes(
            raw[
                body_start + event_len :
                body_start + event_len + payload_len
            ]
        )
        expected = self._digest(
            sequence, generation, event_b, payload_b, previous
        )
        if not secrets.compare_digest(digest, expected):
            raise ValueError("spatial journal record digest mismatch")
        try:
            event_type = event_b.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("spatial journal event type is not valid UTF-8") from exc
        return JournalRecord(
            sequence=sequence,
            generation=generation,
            event_type=event_type,
            payload=payload_b,
            previous_sha256=previous.hex(),
            sha256=digest.hex(),
        )

    def verify(self) -> bool:
        self.records()
        return True

    def head_sha256(self) -> str:
        raw = self._read_region()
        _next, _count, head = self._header(raw)
        return head.hex()
