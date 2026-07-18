"""
ZDX Parallel Pyxel VM frame synchronization layer.

Provides manifest exchange and integrity verification without changing
execution behavior.
"""

from __future__ import annotations

import hashlib
import os

from zdx_network import ZDXMessage


class FrameSync:
    def __init__(self):
        self.frames = {}

    @staticmethod
    def checksum(path: str) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def register(self, path: str):
        digest = self.checksum(path)
        self.frames[digest] = path
        return digest

    def announce(self, path: str) -> ZDXMessage:
        digest = self.register(path)
        return ZDXMessage(
            kind="frame_announce",
            payload={
                "sha256": digest,
                "filename": os.path.basename(path),
            },
        )

    def verify(self, path: str, expected_hash: str) -> bool:
        return self.checksum(path) == expected_hash

    def request_missing(self, sha256: str) -> ZDXMessage:
        return ZDXMessage(
            kind="frame_request",
            payload={"sha256": sha256},
        )

    def accept(self, message: ZDXMessage) -> bool:
        if message.kind != "frame_announce":
            return False
        return message.payload.get("sha256") in self.frames
