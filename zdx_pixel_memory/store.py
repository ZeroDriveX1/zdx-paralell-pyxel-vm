"""
store.py — Low-level pixel memory store.

Each key maps to a <sanitized>_<md5hash>.px.png file inside the storage directory.
A keys.json index file maps original keys → filenames, preventing collisions when
two different keys sanitize to the same slug.

Migration shim: files written by the old scheme (no hash suffix) are readable via
fallback path lookup. They will not appear in keys() until re-written.
"""

import hashlib
import json
import os
import re

from . import codec


class PixelStore:
    """
    Key/value store backed entirely by PNG pixel files.

    Parameters
    ----------
    store_dir : str
        Directory where .px.png files and keys.json are kept. Created on first use.
    """

    _SAFE_KEY = re.compile(r"[^\w\-.]")
    _INDEX_FILE = "keys.json"

    def __init__(self, store_dir: str = "zdx_memory/"):
        self.store_dir = store_dir
        os.makedirs(store_dir, exist_ok=True)
        self._index: dict = self._load_index()

    # ------------------------------------------------------------------
    # Index management
    # ------------------------------------------------------------------

    def _index_path(self) -> str:
        return os.path.join(self.store_dir, self._INDEX_FILE)

    def _load_index(self) -> dict:
        path = self._index_path()
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r") as f:
                return json.load(f)
        except json.JSONDecodeError as exc:
            print(f"[WARN] PixelStore: corrupt index '{path}', starting fresh: {exc}")
            return {}
        except Exception as exc:
            print(f"[WARN] PixelStore: could not load index '{path}', starting fresh: {exc}")
            return {}

    def _save_index(self):
        try:
            with open(self._index_path(), "w") as f:
                json.dump(self._index, f, indent=2)
        except Exception as exc:
            raise RuntimeError(
                f"PixelStore: failed to save index '{self._index_path()}': {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Path resolution
    # ------------------------------------------------------------------

    def _make_filename(self, key: str) -> str:
        """Collision-safe filename: sanitized slug + 8-char MD5 of original key."""
        safe = self._SAFE_KEY.sub("_", key)
        suffix = hashlib.md5(key.encode()).hexdigest()[:8]
        return f"{safe}_{suffix}.px.png"

    def _old_filename(self, key: str) -> str:
        """Old scheme (no hash): used only as migration read fallback."""
        safe = self._SAFE_KEY.sub("_", key)
        return f"{safe}.px.png"

    def _old_path(self, key: str) -> str:
        return os.path.join(self.store_dir, self._old_filename(key))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write(self, key: str, value) -> str:
        """Encode *value* and save it as a PNG. Returns the file path."""
        if key not in self._index:
            self._index[key] = self._make_filename(key)
            self._save_index()
        path = os.path.join(self.store_dir, self._index[key])
        img = codec.encode(value)
        img.save(path)
        return path

    def read(self, key: str, default=None):
        """Return the decoded value for *key*, or *default* if not found."""
        from PIL import Image

        # Primary: index-based path
        if key in self._index:
            path = os.path.join(self.store_dir, self._index[key])
            if os.path.exists(path):
                return codec.decode(Image.open(path))

        # Migration shim: fall back to old-scheme filename
        old = self._old_path(key)
        if os.path.exists(old):
            return codec.decode(Image.open(old))

        return default

    def delete(self, key: str) -> bool:
        """Delete the PNG for *key*. Returns True if anything was removed."""
        removed = False

        if key in self._index:
            path = os.path.join(self.store_dir, self._index[key])
            if os.path.exists(path):
                os.remove(path)
                removed = True
            del self._index[key]
            self._save_index()

        # Also clean up any old-scheme file for this key
        old = self._old_path(key)
        if os.path.exists(old):
            os.remove(old)
            removed = True

        return removed

    def exists(self, key: str) -> bool:
        if key in self._index:
            return os.path.exists(os.path.join(self.store_dir, self._index[key]))
        return os.path.exists(self._old_path(key))

    def keys(self) -> list:
        """Return all keys written through this store (from index)."""
        return sorted(self._index.keys())

    def all(self) -> dict:
        """Return every key/value pair as a plain dict."""
        return {k: self.read(k) for k in self.keys()}
