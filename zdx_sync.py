"""
ZDX Parallel Pyxel VM frame synchronization layer.

Frames remain identified by SHA-256 of the exact PNG bytes. Spatial geometry is
announced as protocol metadata only; synchronization never rewrites or translates
the executable raster.
"""

from __future__ import annotations

import hashlib
import os

from zdx_network import ZDXMessage


class FrameSync:
    def __init__(self):
        self.frames = {}
        self.frame_metadata = {}

    @staticmethod
    def checksum(path: str) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _normalize_layout(layout):
        if layout is None:
            return None
        if hasattr(layout, "to_dict"):
            return layout.to_dict()
        if isinstance(layout, dict):
            return dict(layout)
        raise TypeError("spatial_layout must be a dict, expose to_dict(), or be None")

    def register(self, path: str, *, spatial_layout=None):
        digest = self.checksum(path)
        self.frames[digest] = path
        layout = self._normalize_layout(spatial_layout)
        self.frame_metadata[digest] = {
            "execution_model": "spatial-png" if layout is not None else "pixel-frame",
            "spatial_layout": layout,
        }
        return digest

    def announce(self, path: str, *, spatial_layout=None) -> ZDXMessage:
        digest = self.register(path, spatial_layout=spatial_layout)
        metadata = self.frame_metadata[digest]
        payload = {
            "sha256": digest,
            "filename": os.path.basename(path),
            "execution_model": metadata["execution_model"],
        }
        if metadata["spatial_layout"] is not None:
            payload["spatial_version"] = 1
            payload["spatial_layout"] = metadata["spatial_layout"]
        return ZDXMessage(kind="frame_announce", payload=payload)

    def verify(self, path: str, expected_hash: str) -> bool:
        return self.checksum(path) == expected_hash

    def request_missing(self, sha256: str) -> ZDXMessage:
        return ZDXMessage(kind="frame_request", payload={"sha256": sha256})

    def accept(self, message: ZDXMessage) -> bool:
        if message.kind != "frame_announce":
            return False
        return message.payload.get("sha256") in self.frames
