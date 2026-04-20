"""
pyxel_registry.py — Session-scoped plugin registry for the Parallel Pyxel ecosystem.

The registry lives entirely outside the VM. zdx_parallel_vm.py and
zdx_pixel_memory/ must never import or reference this module.

No persistence. No autowiring. No magic. Explicit registration only.
"""


class PyxelRegistry:
    """
    Lightweight session-scoped registry for attaching modules, bridges,
    backends, and handlers around the Parallel Pyxel VM without modifying it.

    Objects are identified by string names and live only for the duration
    of the process. There is no serialization, no file I/O, and no coupling
    to any specific implementation.

    Example
    -------
        registry = PyxelRegistry()
        registry.register("vm", my_vm_instance)
        registry.register("memory", my_memory_instance)

        vm = registry.get("vm")
        registry.unregister("memory")
        print(registry.registered())   # ["vm"]
        registry.clear()
    """

    def __init__(self):
        self._registry: dict = {}

    def register(self, name: str, obj) -> None:
        """
        Register an object under *name*.

        Raises
        ------
        ValueError
            If *name* is already registered. Call unregister() first to replace.
        """
        if not isinstance(name, str):
            raise TypeError(
                f"PyxelRegistry: name must be a str, got {type(name).__name__!r}"
            )
        if name in self._registry:
            raise ValueError(
                f"PyxelRegistry: '{name}' is already registered. "
                f"Call unregister('{name}') first to replace it."
            )
        self._registry[name] = obj

    def get(self, name: str):
        """
        Retrieve the object registered under *name*.

        Raises
        ------
        KeyError
            If *name* is not registered.
        """
        if name not in self._registry:
            raise KeyError(f"PyxelRegistry: '{name}' is not registered.")
        return self._registry[name]

    def unregister(self, name: str) -> None:
        """
        Remove the object registered under *name*.

        Raises
        ------
        KeyError
            If *name* is not registered.
        """
        if name not in self._registry:
            raise KeyError(f"PyxelRegistry: '{name}' is not registered.")
        del self._registry[name]

    def registered(self) -> list:
        """Return a sorted list of all currently registered names."""
        return sorted(self._registry.keys())

    def clear(self) -> None:
        """Remove all registered objects."""
        self._registry.clear()

    def __repr__(self) -> str:
        return f"PyxelRegistry(registered={self.registered()})"
