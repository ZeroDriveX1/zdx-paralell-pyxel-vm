"""
zdx_agent_runtime.py — Reference consumer showing how PyxelRegistry coordinates
the Parallel Pyxel VM and agent memory without coupling them.

ZDXAgentRuntime never imports ParallelPyxelVM or ZDXAgentMemory directly.
It retrieves them from the registry by name and calls their interfaces.
This means any object with a compatible interface can be swapped in —
a mock vm, an alternate memory backend, a logging proxy, etc.

Expected registry keys
----------------------
    "vm"      (required) — any object with:
                  execute_texture(image_path: str) -> dict
                  .shared     : dict
                  .registers  : dict

    "memory"  (optional) — any object with:
                  remember(key: str, value: any) -> any
"""

from pyxel_registry import PyxelRegistry


class ZDXAgentRuntime:
    """
    Thin coordination layer that runs a VM program and optionally persists
    execution state to an agent memory backend.

    Both the VM and the memory backend are resolved from the registry at
    call time, so they can be swapped without touching this class.

    Parameters
    ----------
    registry : PyxelRegistry
        A populated registry containing at least a "vm" entry.
    """

    def __init__(self, registry: PyxelRegistry):
        self.registry = registry

    def run(self, image_path: str) -> dict:
        """
        Execute *image_path* on the registered VM and optionally persist state.

        Steps
        -----
        1. Retrieve vm from registry.get("vm").
        2. Retrieve mem from registry.get("memory") if registered (optional).
        3. Call vm.execute_texture(image_path).
        4. If mem is registered, persist vm.shared under "shared_state" and
           vm.registers under "register_state".
        5. Return vm.registers.

        Parameters
        ----------
        image_path : str
            Path to the program PNG to execute.

        Returns
        -------
        dict
            The VM's register state after execution.
        """
        vm = self.registry.get("vm")

        mem = None
        try:
            mem = self.registry.get("memory")
        except KeyError:
            pass

        vm.execute_texture(image_path)

        if mem is not None:
            mem.remember("shared_state", dict(vm.shared))
            mem.remember("register_state", {k: dict(v) for k, v in vm.registers.items()})

        return vm.registers


if __name__ == "__main__":
    # Usage example — imports are local to __main__ so the module itself
    # stays decoupled from any specific VM or memory implementation.
    from zdx_parallel_vm import ParallelPyxelVM, SimpleCompiler
    from zdx_pixel_memory import ZDXAgentMemory
    import tempfile, os

    # Build a tiny program so the example is self-contained
    compiler = SimpleCompiler()
    prog = [["SET_A 10", "SET_B 5", "ADD", "STORE_MEM 0", "HALT"]]
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.close()
    compiler.compile(prog, tmp.name)

    # --- Example 1: vm + memory backend ---
    registry = PyxelRegistry()
    registry.register("vm", ParallelPyxelVM(threads=1))
    registry.register("memory", ZDXAgentMemory(agent_id="example_agent"))

    runtime = ZDXAgentRuntime(registry)
    result = runtime.run(tmp.name)

    print("registers:", result)
    mem = registry.get("memory")
    print("persisted shared_state:", mem.recall("shared_state"))
    print("persisted register_state:", mem.recall("register_state"))

    # --- Example 2: vm only — no memory backend ---
    registry2 = PyxelRegistry()
    registry2.register("vm", ParallelPyxelVM(threads=1))

    runtime2 = ZDXAgentRuntime(registry2)
    result2 = runtime2.run(tmp.name)
    print("vm-only result:", result2)

    os.unlink(tmp.name)
