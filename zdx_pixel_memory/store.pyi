"""
store.pyi — Public API stubs for PixelStore.

Collision-safe key/value store backed by PNG pixel files.
Each key maps to a <sanitized>_<md5hash>.px.png file inside store_dir.
A keys.json index maintains the original-key → filename mapping.
"""

from typing import Any

class PixelStore:
    store_dir: str

    def __init__(self, store_dir: str = ...) -> None: ...

    def write(self, key: str, value: Any) -> str: ...
    """Encode value as a PNG and save it. Returns the file path."""

    def read(self, key: str, default: Any = ...) -> Any: ...
    """Return the decoded value for key, or default if not found."""

    def delete(self, key: str) -> bool: ...
    """Delete the PNG for key. Returns True if anything was removed."""

    def exists(self, key: str) -> bool: ...

    def keys(self) -> list: ...
    """Return all indexed keys, sorted."""

    def all(self) -> dict: ...
    """Return all key/value pairs as a plain dict."""
