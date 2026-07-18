"""
ZDX frame manifest synchronization helpers.

Tracks deterministic frame metadata before any future distribution layer.
"""

from __future__ import annotations

import hashlib
import time


class ZDXFrameManifest:
    def __init__(self, frame_data: bytes):
        self.frame_hash = hashlib.sha256(frame_data).hexdigest()
        self.size = len(frame_data)
        self.created = time.time()

    def payload(self):
        return {
            "hash": self.frame_hash,
            "size": self.size,
            "created": self.created,
        }

    def verify(self, frame_data: bytes):
        return hashlib.sha256(frame_data).hexdigest() == self.frame_hash
