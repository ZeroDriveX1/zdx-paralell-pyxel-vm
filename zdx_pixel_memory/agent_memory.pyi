"""
agent_memory.pyi — Public API stubs for ZDXAgentMemory.

Pixel-backed key/value memory for a single ZerodriveX agent instance.
All values are stored as .px.png files — no plain-text files on disk.
Any JSON-serializable value can be stored.
"""

from typing import Any

class ZDXAgentMemory:
    agent_id: str

    def __init__(
        self,
        agent_id: str = ...,
        base_dir: str = ...,
    ) -> None: ...

    # ── Core operations ────────────────────────────────────────────────────

    def remember(self, key: str, value: Any) -> str: ...
    """Store value under key. Returns the path to the saved PNG."""

    def recall(self, key: str, default: Any = ...) -> Any: ...
    """Retrieve value for key, or default if not found."""

    def forget(self, key: str) -> bool: ...
    """Remove key. Returns True if it existed."""

    def exists(self, key: str) -> bool: ...

    # ── Bulk helpers ───────────────────────────────────────────────────────

    def keys(self) -> list: ...
    """Return all stored keys, sorted."""

    def snapshot(self) -> dict: ...
    """Return all memory entries as a plain dict (for LLM prompt injection)."""

    def update(self, data: dict) -> None: ...
    """Write multiple key/value pairs at once."""

    def clear(self) -> None: ...
    """Erase all memory for this agent."""

    # ── List helpers ───────────────────────────────────────────────────────

    def append(self, key: str, item: Any) -> None: ...
    """Append item to the list at key. Creates the list if absent."""

    def recall_list(self, key: str) -> list: ...
    """Return the list at key, or [] if not found. Raises TypeError if not a list."""
