"""Bounded binary mailboxes stored directly in spatial PNG regions."""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

from zdx_spatial_frame import SpatialFrame


_MAGIC = b"ZDXMBX1\x00"
_VERSION = 1
_HEADER = struct.Struct(">8sBBHIIQQ")
_SLOT_HEADER = struct.Struct(">QBIHHH32s")
_HEADER_SIZE = _HEADER.size
_SLOT_HEADER_SIZE = _SLOT_HEADER.size
_DEFAULT_SLOT_SIZE = 256
_DOMAIN = b"zdx-spatial-mailbox-v1\x00"


@dataclass(frozen=True)
class MailboxMessage:
    sequence: int
    sender: str
    recipient: str
    topic: str
    payload: bytes
    sha256: str


class SpatialMailbox:
    """FIFO ring queue backed by one declared non-executable region."""

    def __init__(
        self,
        frame: SpatialFrame,
        region: str,
        *,
        slot_size: int = _DEFAULT_SLOT_SIZE,
    ):
        if isinstance(slot_size, bool) or not isinstance(slot_size, int):
            raise ValueError("mailbox slot_size must be an integer")
        if slot_size <= _SLOT_HEADER_SIZE:
            raise ValueError("mailbox slot_size is too small")
        self.frame = frame
        self.region = region
        target = frame.layout.region(region)
        if target.y < frame.layout.execution_rows:
            raise ValueError("mailbox region overlaps execution plane")
        self.capacity_bytes = target.capacity_bytes
        self.slot_size = slot_size
        self.slot_count = (self.capacity_bytes - _HEADER_SIZE) // slot_size
        if self.slot_count < 1:
            raise ValueError("mailbox region is too small for one slot")
        self._ensure_header()

    @property
    def payload_capacity(self) -> int:
        return self.slot_size - _SLOT_HEADER_SIZE

    @staticmethod
    def _message_digest(
        sequence: int,
        sender: bytes,
        recipient: bytes,
        topic: bytes,
        payload: bytes,
    ) -> bytes:
        h = hashlib.sha256()
        h.update(_DOMAIN)
        h.update(sequence.to_bytes(8, "big"))
        for item in (sender, recipient, topic, payload):
            h.update(len(item).to_bytes(4, "big"))
            h.update(item)
        return h.digest()

    def _read_region(self) -> bytearray:
        return bytearray(self.frame.read_bytes(self.capacity_bytes, region=self.region))

    def _write_region(self, raw: bytearray) -> None:
        if len(raw) != self.capacity_bytes:
            raise ValueError("mailbox region image has incorrect size")
        self.frame.write_bytes(bytes(raw), region=self.region)

    def _header(self, raw: bytearray) -> tuple[int, int]:
        magic, version, flags, reserved, slot_size, slot_count, next_seq, read_seq = _HEADER.unpack_from(raw, 0)
        if magic != _MAGIC or version != _VERSION or flags != 0 or reserved != 0:
            raise ValueError("corrupt or unsupported spatial mailbox header")
        if slot_size != self.slot_size or slot_count != self.slot_count:
            raise ValueError("spatial mailbox geometry does not match existing header")
        if read_seq < 1 or next_seq < read_seq or next_seq - read_seq > slot_count:
            raise ValueError("corrupt spatial mailbox sequence state")
        return next_seq, read_seq

    def _write_header(self, raw: bytearray, next_seq: int, read_seq: int) -> None:
        _HEADER.pack_into(
            raw, 0, _MAGIC, _VERSION, 0, 0,
            self.slot_size, self.slot_count, next_seq, read_seq,
        )

    def _ensure_header(self) -> None:
        raw = self._read_region()
        if raw[:_HEADER_SIZE] == b"\x00" * _HEADER_SIZE:
            self._write_header(raw, 1, 1)
            self._write_region(raw)
            return
        self._header(raw)

    def pending_count(self) -> int:
        raw = self._read_region()
        next_seq, read_seq = self._header(raw)
        return next_seq - read_seq

    def _slot_offset(self, sequence: int) -> int:
        index = (sequence - 1) % self.slot_count
        return _HEADER_SIZE + index * self.slot_size

    def enqueue(
        self,
        payload: bytes,
        *,
        sender: str,
        recipient: str,
        topic: str = "",
    ) -> MailboxMessage:
        sender_b = sender.encode("utf-8")
        recipient_b = recipient.encode("utf-8")
        topic_b = topic.encode("utf-8")
        payload_b = bytes(payload)
        if not sender_b or not recipient_b:
            raise ValueError("mailbox sender and recipient are required")
        if any(len(value) > 0xFFFF for value in (sender_b, recipient_b, topic_b)):
            raise ValueError("mailbox metadata field is too large")
        body_len = len(sender_b) + len(recipient_b) + len(topic_b) + len(payload_b)
        if body_len > self.payload_capacity:
            raise ValueError(
                f"mailbox message requires {body_len} bytes; slot payload capacity is "
                f"{self.payload_capacity}"
            )

        raw = self._read_region()
        next_seq, read_seq = self._header(raw)
        if next_seq - read_seq >= self.slot_count:
            raise BufferError("spatial mailbox is full")
        sequence = next_seq
        digest = self._message_digest(sequence, sender_b, recipient_b, topic_b, payload_b)
        offset = self._slot_offset(sequence)
        _SLOT_HEADER.pack_into(
            raw, offset,
            sequence, 1, len(payload_b),
            len(sender_b), len(recipient_b), len(topic_b), digest,
        )
        body = sender_b + recipient_b + topic_b + payload_b
        body_start = offset + _SLOT_HEADER_SIZE
        raw[body_start : body_start + self.payload_capacity] = b"\x00" * self.payload_capacity
        raw[body_start : body_start + len(body)] = body
        self._write_header(raw, next_seq + 1, read_seq)
        self._write_region(raw)
        return MailboxMessage(
            sequence=sequence,
            sender=sender,
            recipient=recipient,
            topic=topic,
            payload=payload_b,
            sha256=digest.hex(),
        )

    def peek(self) -> MailboxMessage | None:
        raw = self._read_region()
        next_seq, read_seq = self._header(raw)
        if read_seq >= next_seq:
            return None
        return self._decode_slot(raw, read_seq)

    def dequeue(self) -> MailboxMessage | None:
        raw = self._read_region()
        next_seq, read_seq = self._header(raw)
        if read_seq >= next_seq:
            return None
        message = self._decode_slot(raw, read_seq)
        offset = self._slot_offset(read_seq)
        raw[offset : offset + self.slot_size] = b"\x00" * self.slot_size
        self._write_header(raw, next_seq, read_seq + 1)
        self._write_region(raw)
        return message

    def _decode_slot(self, raw: bytearray, expected_sequence: int) -> MailboxMessage:
        offset = self._slot_offset(expected_sequence)
        sequence, state, payload_len, sender_len, recipient_len, topic_len, digest = _SLOT_HEADER.unpack_from(raw, offset)
        if state != 1 or sequence != expected_sequence:
            raise ValueError("corrupt spatial mailbox slot sequence/state")
        total = sender_len + recipient_len + topic_len + payload_len
        if total > self.payload_capacity:
            raise ValueError("corrupt spatial mailbox slot length")
        cursor = offset + _SLOT_HEADER_SIZE
        body = bytes(raw[cursor : cursor + total])
        a = sender_len
        b = a + recipient_len
        c = b + topic_len
        sender_b, recipient_b, topic_b, payload = body[:a], body[a:b], body[b:c], body[c:]
        expected = self._message_digest(sequence, sender_b, recipient_b, topic_b, payload)
        if not hashlib.sha256(digest).digest() == hashlib.sha256(expected).digest():
            raise ValueError("spatial mailbox message digest mismatch")
        try:
            sender = sender_b.decode("utf-8")
            recipient = recipient_b.decode("utf-8")
            topic = topic_b.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("spatial mailbox metadata is not valid UTF-8") from exc
        return MailboxMessage(
            sequence=sequence,
            sender=sender,
            recipient=recipient,
            topic=topic,
            payload=payload,
            sha256=digest.hex(),
        )
