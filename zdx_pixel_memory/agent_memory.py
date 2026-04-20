"""
agent_memory.py — High-level memory interface for ZerodriveX AI agents.

Usage
-----
    from zdx_pixel_memory import ZDXAgentMemory

    mem = ZDXAgentMemory(agent_id="agent_01")
    mem.remember("user_name", "Zara")
    mem.remember("task_history", ["search docs", "draft reply"])

    name = mem.recall("user_name")          # "Zara"
    ctx  = mem.snapshot()                   # full dict, ready to inject into prompt
    mem.forget("task_history")
"""

from .store import PixelStore


class ZDXAgentMemory:
    """
    Pixel-backed memory for a single ZerodriveX agent instance.

    All values are stored as PNG files — no plain-text files on disk.
    Any JSON-serializable value (str, int, float, list, dict, bool, None)
    can be stored.

    Parameters
    ----------
    agent_id : str
        Unique identifier for the agent.  Used as the store sub-directory.
    base_dir : str
        Root directory for all agent memory stores.
    """

    def __init__(self, agent_id: str = "default", base_dir: str = "zdx_memory/"):
        self.agent_id = agent_id
        self._store = PixelStore(store_dir=f"{base_dir}{agent_id}/")

    # ------------------------------------------------------------------
    # Core memory operations
    # ------------------------------------------------------------------

    def remember(self, key: str, value) -> str:
        """
        Store *value* under *key*.  Overwrites any existing value.
        Returns the path to the saved PNG.
        """
        path = self._store.write(key, value)
        return path

    def recall(self, key: str, default=None):
        """
        Retrieve the value stored under *key*.
        Returns *default* if the key does not exist.
        """
        return self._store.read(key, default=default)

    def forget(self, key: str) -> bool:
        """
        Remove *key* from memory.  Returns True if it existed.
        """
        return self._store.delete(key)

    def exists(self, key: str) -> bool:
        return self._store.exists(key)

    # ------------------------------------------------------------------
    # Bulk / context helpers
    # ------------------------------------------------------------------

    def keys(self) -> list:
        """Return all keys currently in memory."""
        return self._store.keys()

    def snapshot(self) -> dict:
        """
        Return every memory entry as a plain dict.
        Use this to inject the full memory context into an LLM prompt.
        """
        return self._store.all()

    def update(self, data: dict):
        """Write multiple key/value pairs at once."""
        for key, value in data.items():
            self._store.write(key, value)

    def clear(self):
        """Erase all memories for this agent."""
        for key in self._store.keys():
            self._store.delete(key)

    # ------------------------------------------------------------------
    # List helpers — for append-style memory (e.g. conversation turns)
    # ------------------------------------------------------------------

    def append(self, key: str, item):
        """
        Append *item* to the list stored at *key*.
        Creates the list if it does not yet exist.
        """
        current = self._store.read(key, default=[])
        if not isinstance(current, list):
            raise TypeError(f"Memory key '{key}' is not a list")
        current.append(item)
        self._store.write(key, current)

    def recall_list(self, key: str) -> list:
        """Return the list at *key*, or [] if not found."""
        value = self._store.read(key, default=[])
        if not isinstance(value, list):
            raise TypeError(f"Memory key '{key}' is not a list")
        return value

    # ------------------------------------------------------------------
    # Repr
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        keys = self._store.keys()
        return f"ZDXAgentMemory(agent_id={self.agent_id!r}, keys={keys})"
