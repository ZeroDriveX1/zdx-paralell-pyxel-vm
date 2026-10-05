"""Spatial PNG-backed key/value store.

Unlike PixelStore, which writes one PNG per key, SpatialPixelStore packs an entire
JSON key/value dictionary into the storage plane of one standards-valid RGB PNG.
The raster location is the storage address; no sidecar index is required.
"""

from __future__ import annotations

import os

from zdx_spatial_frame import SpatialFrame, SpatialLayout


class SpatialPixelStore:
    """Key/value store packed into one spatial PNG data plane.

    The interface intentionally mirrors ``PixelStore`` so higher-level agent
    memory can switch backends without changing call sites.
    """

    def __init__(
        self,
        path: str = "zdx_memory.spatial.png",
        *,
        width: int = 256,
        height: int = 256,
        execution_rows: int = 0,
    ):
        self.path = path
        self.layout = SpatialLayout(
            width=width,
            height=height,
            execution_rows=execution_rows,
        )
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        if not os.path.exists(path):
            SpatialFrame.blank(self.layout).save(path)

    @property
    def capacity_bytes(self) -> int:
        return self.layout.storage_capacity_bytes

    def _read_all(self) -> dict:
        frame = SpatialFrame.open(self.path, self.layout)
        value = frame.read_json()
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("SpatialPixelStore root payload is not a dictionary")
        return value

    def _write_all(self, values: dict) -> None:
        frame = SpatialFrame.open(self.path, self.layout)
        frame.write_json(values)
        frame.save(self.path)

    def write(self, key: str, value) -> str:
        values = self._read_all()
        values[key] = value
        self._write_all(values)
        return self.path

    def read(self, key: str, default=None):
        return self._read_all().get(key, default)

    def delete(self, key: str) -> bool:
        values = self._read_all()
        if key not in values:
            return False
        del values[key]
        self._write_all(values)
        return True

    def exists(self, key: str) -> bool:
        return key in self._read_all()

    def keys(self) -> list:
        return sorted(self._read_all().keys())

    def all(self) -> dict:
        return self._read_all()

    def clear(self) -> None:
        self._write_all({})
