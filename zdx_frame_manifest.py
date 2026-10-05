"""ZDX frame manifest synchronization helpers.

Frame identity remains a SHA-256 over the exact PNG bytes. Spatial metadata is
carried beside that identity so a receiver can validate geometry before execution
without changing the PNG or translating the raster into another representation.
"""

from __future__ import annotations

import hashlib
import time

from zdx_spatial_frame import SPATIAL_FRAME_VERSION, SpatialLayout


class ZDXFrameManifest:
    def __init__(
        self,
        frame_data: bytes,
        *,
        frame_format: str = "png",
        spatial_layout=None,
        spatial_version: int = SPATIAL_FRAME_VERSION,
    ):
        self.frame_hash = hashlib.sha256(frame_data).hexdigest()
        self.size = len(frame_data)
        self.created = time.time()
        self.frame_format = frame_format
        self.spatial_layout = self._normalize_layout(spatial_layout)
        if self.spatial_layout is not None and (
            isinstance(spatial_version, bool)
            or not isinstance(spatial_version, int)
            or spatial_version != SPATIAL_FRAME_VERSION
        ):
            raise ValueError(
                f"unsupported spatial_version {spatial_version!r}; "
                f"expected {SPATIAL_FRAME_VERSION}"
            )
        self.spatial_version = SPATIAL_FRAME_VERSION if self.spatial_layout is not None else None
        self.execution_model = "spatial-png" if self.spatial_layout is not None else "pixel-frame"

    @staticmethod
    def _normalize_layout(layout):
        if layout is None:
            return None
        if hasattr(layout, "to_dict"):
            layout = layout.to_dict()
        if isinstance(layout, dict):
            return SpatialLayout.from_dict(layout).to_dict()
        raise TypeError("spatial_layout must be a dict, expose to_dict(), or be None")

    def payload(self):
        payload = {
            "hash": self.frame_hash,
            "size": self.size,
            "created": self.created,
            "frame_format": self.frame_format,
            "execution_model": self.execution_model,
        }
        if self.spatial_layout is not None:
            payload["spatial_version"] = self.spatial_version
            payload["spatial_layout"] = dict(self.spatial_layout)
        return payload

    def verify(self, frame_data: bytes):
        return hashlib.sha256(frame_data).hexdigest() == self.frame_hash
