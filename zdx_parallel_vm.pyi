"""
zdx_parallel_vm.pyi — Public API stubs for the Parallel Pyxel VM.

Distributed alongside the compiled binary to provide IDE type-checking
and auto-completion for licensees without exposing implementation.
"""

from typing import Any, Optional

# ── Module-level constants ───────────────────────────────────────────────────

MAP: dict
REVERSE_MAP: dict

# ── ParallelPyxelVM ──────────────────────────────────────────────────────────

class ParallelPyxelVM:
    threads: int
    debug: bool
    persist_shared: bool
    shared_path: str
    strict_mode: bool
    max_chain: int
    max_col_steps: int
    clock: int
    registers: dict
    shared: dict
    mem_map: dict
    next_frame: Optional[str]
    frame_registry: dict

    def __init__(
        self,
        threads: int = ...,
        debug: bool = ...,
        persist_shared: bool = ...,
        shared_path: str = ...,
        strict_mode: bool = ...,
        map: Optional[dict] = ...,
        max_chain: int = ...,
        max_col_steps: int = ...,
    ) -> None: ...

    def execute_texture(self, image_path: str) -> dict: ...
    def dump_state(self) -> None: ...
    def register_frame(self, index: int, path: str) -> None: ...
    def set_mem_map(self, mapping: dict) -> None: ...

# ── ThoughtCompiler ──────────────────────────────────────────────────────────

class ThoughtCompiler:
    mapkey: dict

    def __init__(
        self,
        mapkey: Optional[dict] = ...,
        map: Optional[dict] = ...,
    ) -> None: ...

    def compile_thought_matrix(
        self,
        parallel_instructions: list,
        out_path: str,
    ) -> None: ...

# ── SimpleCompiler ───────────────────────────────────────────────────────────

class SimpleCompiler:
    def __init__(self, map: Optional[dict] = ...) -> None: ...
    def compile(
        self,
        program: list,
        out_path: str,
        debug: bool = ...,
    ) -> str: ...

# ── VisualMemoryManager ──────────────────────────────────────────────────────

class VisualMemoryManager:
    brain_dir: str
    memory_metadata: dict

    def __init__(self, brain_dir: str = ...) -> None: ...
    def register_frame(self, name: str, score: int) -> None: ...
    def update_decay(self) -> None: ...
    def prune_frame(self, frame_name: str) -> None: ...

# ── FrequencyChallenge ───────────────────────────────────────────────────────

class FrequencyChallenge:
    key_row: int

    def __init__(self, key_row: int = ...) -> None: ...
    def generate_challenge_frame(self, base_path: str) -> int: ...

# ── Module-level utilities ───────────────────────────────────────────────────

def assert_vm_state(vm: ParallelPyxelVM, expected: dict) -> None: ...
def build_compute_and_store(a: int, b: int, mem_idx: int) -> list: ...
def build_conditional_write(mem_idx: int, compare_val: int) -> list: ...
def build_validation_pattern() -> list: ...
