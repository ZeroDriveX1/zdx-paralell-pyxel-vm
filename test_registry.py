"""
test_registry.py — Tests for PyxelRegistry and ZDXAgentRuntime.

Covers:
  - register / get round-trip
  - ValueError on duplicate registration
  - KeyError on get of unregistered name
  - unregister then get raises KeyError
  - registered() returns sorted list
  - clear() empties the registry
  - ZDXAgentRuntime.run() with vm + memory
  - ZDXAgentRuntime.run() with vm only (memory optional)
  - Swapping a mock vm and confirming runtime uses it
"""

import os
import tempfile
import unittest

from pyxel_registry import PyxelRegistry
from zdx_agent_runtime import ZDXAgentRuntime


# ---------------------------------------------------------------------------
# Minimal mock objects — no dependency on zdx_parallel_vm or zdx_pixel_memory
# ---------------------------------------------------------------------------

class MockVM:
    """Minimal VM-compatible object for runtime tests."""

    def __init__(self, shared=None, registers=None):
        self.shared = shared or {"M0": 42, "M1": 0}
        self.registers = registers or {"T0": {"A": 7, "B": 3, "OUT": 10, "T": 2}}
        self.last_path = None

    def execute_texture(self, image_path: str) -> dict:
        self.last_path = image_path
        return self.registers


class MockMemory:
    """Minimal memory-compatible object for runtime tests."""

    def __init__(self):
        self._store: dict = {}

    def remember(self, key: str, value) -> None:
        self._store[key] = value

    def recall(self, key: str, default=None):
        return self._store.get(key, default)


# ---------------------------------------------------------------------------
# PyxelRegistry tests
# ---------------------------------------------------------------------------

class TestPyxelRegistryRegisterGet(unittest.TestCase):

    def test_register_and_get_round_trip(self):
        r = PyxelRegistry()
        obj = object()
        r.register("thing", obj)
        self.assertIs(r.get("thing"), obj)

    def test_register_different_types(self):
        r = PyxelRegistry()
        r.register("num", 42)
        r.register("lst", [1, 2, 3])
        r.register("dct", {"a": 1})
        self.assertEqual(r.get("num"), 42)
        self.assertEqual(r.get("lst"), [1, 2, 3])
        self.assertEqual(r.get("dct"), {"a": 1})

    def test_duplicate_registration_raises_value_error(self):
        r = PyxelRegistry()
        r.register("vm", MockVM())
        with self.assertRaises(ValueError):
            r.register("vm", MockVM())

    def test_get_unregistered_raises_key_error(self):
        r = PyxelRegistry()
        with self.assertRaises(KeyError):
            r.get("nonexistent")


class TestPyxelRegistryUnregister(unittest.TestCase):

    def test_unregister_then_get_raises_key_error(self):
        r = PyxelRegistry()
        r.register("x", 1)
        r.unregister("x")
        with self.assertRaises(KeyError):
            r.get("x")

    def test_unregister_nonexistent_raises_key_error(self):
        r = PyxelRegistry()
        with self.assertRaises(KeyError):
            r.unregister("ghost")

    def test_unregister_then_reregister_succeeds(self):
        r = PyxelRegistry()
        r.register("slot", "first")
        r.unregister("slot")
        r.register("slot", "second")
        self.assertEqual(r.get("slot"), "second")


class TestPyxelRegistryRegisteredAndClear(unittest.TestCase):

    def test_registered_returns_sorted_list(self):
        r = PyxelRegistry()
        r.register("zoo", 1)
        r.register("alpha", 2)
        r.register("middle", 3)
        self.assertEqual(r.registered(), ["alpha", "middle", "zoo"])

    def test_registered_empty(self):
        r = PyxelRegistry()
        self.assertEqual(r.registered(), [])

    def test_clear_empties_registry(self):
        r = PyxelRegistry()
        r.register("a", 1)
        r.register("b", 2)
        r.clear()
        self.assertEqual(r.registered(), [])

    def test_clear_then_get_raises_key_error(self):
        r = PyxelRegistry()
        r.register("x", 99)
        r.clear()
        with self.assertRaises(KeyError):
            r.get("x")

    def test_clear_then_reregister(self):
        r = PyxelRegistry()
        r.register("x", 1)
        r.clear()
        r.register("x", 2)
        self.assertEqual(r.get("x"), 2)


# ---------------------------------------------------------------------------
# ZDXAgentRuntime tests
# ---------------------------------------------------------------------------

class TestZDXAgentRuntimeWithMemory(unittest.TestCase):
    """Runtime with both vm and memory registered."""

    def setUp(self):
        self.vm = MockVM()
        self.mem = MockMemory()
        self.registry = PyxelRegistry()
        self.registry.register("vm", self.vm)
        self.registry.register("memory", self.mem)
        self.runtime = ZDXAgentRuntime(self.registry)

    def test_run_returns_vm_registers(self):
        result = self.runtime.run("dummy.png")
        self.assertEqual(result, self.vm.registers)

    def test_run_calls_vm_execute_texture(self):
        self.runtime.run("my_program.png")
        self.assertEqual(self.vm.last_path, "my_program.png")

    def test_run_persists_shared_state(self):
        self.runtime.run("dummy.png")
        self.assertEqual(self.mem.recall("shared_state"), self.vm.shared)

    def test_run_persists_register_state(self):
        self.runtime.run("dummy.png")
        stored = self.mem.recall("register_state")
        self.assertEqual(stored, {"T0": {"A": 7, "B": 3, "OUT": 10, "T": 2}})

    def test_persisted_shared_is_a_copy(self):
        """Mutating vm.shared after run must not affect the persisted value."""
        self.runtime.run("dummy.png")
        original = dict(self.mem.recall("shared_state"))
        self.vm.shared["M0"] = 999
        self.assertEqual(self.mem.recall("shared_state"), original)


class TestZDXAgentRuntimeVMOnly(unittest.TestCase):
    """Runtime with only vm registered — memory is optional."""

    def setUp(self):
        self.vm = MockVM()
        self.registry = PyxelRegistry()
        self.registry.register("vm", self.vm)
        self.runtime = ZDXAgentRuntime(self.registry)

    def test_run_succeeds_without_memory(self):
        result = self.runtime.run("dummy.png")
        self.assertEqual(result, self.vm.registers)

    def test_run_without_memory_does_not_raise(self):
        try:
            self.runtime.run("dummy.png")
        except Exception as e:
            self.fail(f"run() raised {type(e).__name__} unexpectedly: {e}")


class TestZDXAgentRuntimeMissingVM(unittest.TestCase):
    """Runtime raises KeyError when no vm is registered."""

    def test_run_without_vm_raises_key_error(self):
        registry = PyxelRegistry()
        runtime = ZDXAgentRuntime(registry)
        with self.assertRaises(KeyError):
            runtime.run("dummy.png")


class TestZDXAgentRuntimeMockSwap(unittest.TestCase):
    """
    Swap a mock vm into the registry and confirm the runtime uses it.
    Demonstrates that ZDXAgentRuntime is fully decoupled from any
    specific VM implementation.
    """

    def test_runtime_uses_registered_vm(self):
        custom_registers = {"T0": {"A": 99, "B": 0, "OUT": 0, "T": 0}}
        mock_vm = MockVM(registers=custom_registers)

        registry = PyxelRegistry()
        registry.register("vm", mock_vm)

        runtime = ZDXAgentRuntime(registry)
        result = runtime.run("any_path.png")

        self.assertEqual(result, custom_registers)
        self.assertEqual(mock_vm.last_path, "any_path.png")

    def test_swap_vm_between_runs(self):
        """Unregister old vm, register a new one — runtime picks up the change."""
        vm_a = MockVM(registers={"T0": {"A": 1, "B": 0, "OUT": 0, "T": 0}})
        vm_b = MockVM(registers={"T0": {"A": 2, "B": 0, "OUT": 0, "T": 0}})

        registry = PyxelRegistry()
        registry.register("vm", vm_a)
        runtime = ZDXAgentRuntime(registry)

        result_a = runtime.run("prog.png")
        self.assertEqual(result_a["T0"]["A"], 1)

        registry.unregister("vm")
        registry.register("vm", vm_b)

        result_b = runtime.run("prog.png")
        self.assertEqual(result_b["T0"]["A"], 2)

    def test_memory_backend_can_also_be_mocked(self):
        """Any object with remember() works as a memory backend."""
        calls = []

        class SpyMemory:
            def remember(self, key, value):
                calls.append((key, value))

        registry = PyxelRegistry()
        registry.register("vm", MockVM())
        registry.register("memory", SpyMemory())

        ZDXAgentRuntime(registry).run("dummy.png")

        keys_persisted = [k for k, _ in calls]
        self.assertIn("shared_state", keys_persisted)
        self.assertIn("register_state", keys_persisted)


# ---------------------------------------------------------------------------
# Integration smoke test using real VM + real memory
# ---------------------------------------------------------------------------

class TestRuntimeIntegrationRealObjects(unittest.TestCase):
    """
    End-to-end: real ParallelPyxelVM + real ZDXAgentMemory coordinated
    through PyxelRegistry and ZDXAgentRuntime.
    """

    def test_real_vm_and_memory_through_registry(self):
        from zdx_parallel_vm import ParallelPyxelVM, SimpleCompiler
        from zdx_pixel_memory import ZDXAgentMemory

        # Build a tiny program: SET_A 7, SET_B 3, ADD, STORE_MEM 0, HALT
        compiler = SimpleCompiler()
        prog = [["SET_A 7", "SET_B 3", "ADD", "STORE_MEM 0", "HALT"]]
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.close()
        compiler.compile(prog, tmp.name)

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                registry = PyxelRegistry()
                registry.register("vm", ParallelPyxelVM(threads=1))
                registry.register("memory", ZDXAgentMemory(
                    agent_id="test_agent",
                    base_dir=tmpdir + "/",
                ))

                runtime = ZDXAgentRuntime(registry)
                result = runtime.run(tmp.name)

                # VM executed correctly
                self.assertEqual(result["T0"]["A"], 7)   # SET_A 7
                self.assertEqual(result["T0"]["OUT"], 10) # (7+3) & 0xFF

                # State was persisted to memory
                mem = registry.get("memory")
                shared = mem.recall("shared_state")
                self.assertIsNotNone(shared)
                self.assertEqual(shared["M0"], 7)        # STORE_MEM stores A (=7)

                regs = mem.recall("register_state")
                self.assertIsNotNone(regs)
                self.assertIn("T0", regs)
        finally:
            os.unlink(tmp.name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
