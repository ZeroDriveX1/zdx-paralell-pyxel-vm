"""High-level memory interface for ZDX agents.

The compatibility backend stores one PNG per key. Spatial mode stores the
agent's complete deterministic typed memory map inside one spatial PNG region
and may be bound to a named region in the same frame that carries executable
PyxelVM rows.
"""

from __future__ import annotations

from pathlib import Path
import re

from .spatial_store import SpatialPixelStore
from .store import PixelStore


_SAFE_AGENT_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


class ZDXAgentMemory:
    """Pixel-backed memory for one ZDX agent instance."""

    def __init__(
        self,
        agent_id: str = "default",
        base_dir: str = "zdx_memory/",
        *,
        spatial: bool = False,
        spatial_width: int = 256,
        spatial_height: int = 256,
        spatial_execution_rows: int = 0,
        spatial_path: str | None = None,
        spatial_layout=None,
        spatial_region: str | None = None,
    ):
        if not isinstance(agent_id, str) or not _SAFE_AGENT_ID.fullmatch(agent_id):
            raise ValueError("agent_id must contain only letters, digits, '.', '_' or '-'")
        if agent_id in {".", ".."}:
            raise ValueError("agent_id cannot be a traversal component")
        self.agent_id = agent_id
        self.spatial = bool(spatial)

        if self.spatial:
            agent_dir = Path(base_dir) / agent_id
            path = spatial_path or str(agent_dir / "memory.spatial.png")
            self._store = SpatialPixelStore(
                path=path,
                layout=spatial_layout,
                width=spatial_width,
                height=spatial_height,
                execution_rows=spatial_execution_rows,
                region=spatial_region,
            )
        else:
            if spatial_path is not None or spatial_layout is not None or spatial_region is not None:
                raise ValueError("spatial_path/layout/region require spatial=True")
            self._store = PixelStore(store_dir=str(Path(base_dir) / agent_id))

    @property
    def is_spatial(self) -> bool:
        return self.spatial

    @property
    def capacity_bytes(self):
        return getattr(self._store, "capacity_bytes", None)

    @property
    def frame_path(self) -> str | None:
        return getattr(self._store, "frame_path", None)

    @property
    def layout(self):
        return getattr(self._store, "layout", None)

    @property
    def region(self):
        return getattr(self._store, "region", None)

    def remember(self, key: str, value) -> str:
        """Store a value under key and return the containing PNG path."""
        return self._store.write(key, value)

    def recall(self, key: str, default=None):
        return self._store.read(key, default=default)

    def forget(self, key: str) -> bool:
        return self._store.delete(key)

    def exists(self, key: str) -> bool:
        return self._store.exists(key)

    def keys(self) -> list:
        return self._store.keys()

    def snapshot(self) -> dict:
        """Decode the memory map at an external reasoning/API boundary."""
        return self._store.all()

    def update(self, data: dict):
        if not isinstance(data, dict):
            raise TypeError("memory update requires a dictionary")
        for key, value in data.items():
            self._store.write(key, value)

    def clear(self):
        clear = getattr(self._store, "clear", None)
        if callable(clear):
            clear()
            return
        for key in self._store.keys():
            self._store.delete(key)

    def append(self, key: str, item):
        current = self._store.read(key, default=[])
        if not isinstance(current, list):
            raise TypeError(f"Memory key '{key}' is not a list")
        current.append(item)
        self._store.write(key, current)

    def recall_list(self, key: str) -> list:
        value = self._store.read(key, default=[])
        if not isinstance(value, list):
            raise TypeError(f"Memory key '{key}' is not a list")
        return value

    def generation(self) -> int | None:
        getter = getattr(self._store, "generation", None)
        return getter() if callable(getter) else None

    def __repr__(self) -> str:
        mode = "spatial" if self.spatial else "pixel"
        return f"ZDXAgentMemory(agent_id={self.agent_id!r}, mode={mode!r}, keys={self.keys()})"
