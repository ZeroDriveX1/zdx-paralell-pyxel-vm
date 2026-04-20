import argparse
import json
import math
import os
import random
import sys

import numpy as np
from PIL import Image

MAP = {
    "SET_REG":    (10,  0,  0),
    "SET_B":      (11,  0,  0),
    "NOT":        (12,  0,  0),   # A = (~A) & 0xFF
    "ADD_THREAD": (20,  0,  0),
    "SUB":        (21,  0,  0),   # A = (A - B) & 0xFF
    "MUL":        (22,  0,  0),   # A = (A * B) & 0xFF
    "DIV":        (23,  0,  0),   # A = A // B  (0 if B==0)
    "IF_A_EQ":    (30,  0,  0),
    "CMP":        (31,  0,  0),   # OUT = 1 if A==B else 0  (register B)
    "LOAD_MEM":   (40,  0,  0),
    "STORE_MEM":  (41,  0,  0),
    "VERIFY_FREQ":(50,  0,  0),   # R=50; G=mirror_offset(reserved), B=write_target(reserved)
    "SAVE_STATE": (60,  0,  0),   # R=60; G=frame_index into FrameRegistry
    "JMP":        (70,  0,  0),   # R=70; G=target column (absolute)
    "COPY_OUT":   (24,  0,  0),   # A = OUT  (lets ADD/CMP results be consumed)
    "HALT":       (255, 0,  0),   # R=255; G/B free for future args
}
REVERSE_MAP = {v: k for k, v in MAP.items()}

_OPCODE_NAMES = {
    10: "SET_REG",  11: "SET_B",     12: "NOT",
    20: "ADD_THREAD", 21: "SUB",     22: "MUL",       23: "DIV",
    24: "COPY_OUT",
    30: "IF_A_EQ",  31: "CMP",
    40: "LOAD_MEM", 41: "STORE_MEM",
    50: "VERIFY_FREQ", 60: "SAVE_STATE", 70: "JMP",
    255: "HALT",
}

# Default resource caps. Both are overridable per-VM via the constructor.
# They exist to prevent infinite loops (a buggy or hostile program chaining
# frames forever, or a JMP that never lands on a HALT). Hitting a cap raises
# RuntimeError — silent truncation is a hidden bug, not a graceful degradation.
_DEFAULT_MAX_CHAIN     = 100
_DEFAULT_MAX_COL_STEPS = 4096
_NOP = (0, 0, 0)


def _opcode_name(r, g, b):
    return _OPCODE_NAMES.get(r, f"UNK({r})")


# ---------------------------------------------------------------------------
# ParallelPyxelVM
# ---------------------------------------------------------------------------

class ParallelPyxelVM:
    """
    Pixel-native virtual machine for ZerodriveX AI agents.

    Execution Rules Cheat Sheet
    ---------------------------
    - A        : working register — most ops read/write A
    - B        : secondary operand — written by SET_B, read by ADD
    - OUT      : computation result — written by ADD only
    - T        : clock register — set to current column index automatically
    - M0–M7    : shared memory — cross-thread, sequential within each column

    Data movement:
      SET_A value   → A = value
      SET_B value   → B = value
      LOAD_MEM n    → A = Mn          (reads shared into A)
      STORE_MEM n   → Mn = A          (writes A; insert COPY_OUT first to
                                       store an ADD/CMP result)
      COPY_OUT      → A = OUT         (lift last ADD/CMP result into A)

    Arithmetic:
      ADD           → OUT = (A + B) & 0xFF   (result in OUT, A unchanged)

    Control flow:
      IF_A_EQ val   → if A != val: SKIP next column
                       if A == val: EXECUTE next column
                      (guards the NEXT instruction, not a branch-to-label)

    Thread model:
      - Execution is column-major: all threads execute column X before column X+1
      - HALT freezes one thread; others continue
      - Shared memory writes by T0 at column X are visible to T1 at the same column X
    """

    def __init__(
        self,
        threads: int = 8,
        debug: bool = False,
        persist_shared: bool = False,
        shared_path: str = "shared_state.json",
        strict_mode: bool = False,
        map: dict = None,
        max_chain: int = _DEFAULT_MAX_CHAIN,
        max_col_steps: int = _DEFAULT_MAX_COL_STEPS,
    ):
        if threads < 1:
            raise ValueError(f"ParallelPyxelVM: threads must be >= 1, got {threads}")
        if max_chain < 1:
            raise ValueError(f"ParallelPyxelVM: max_chain must be >= 1, got {max_chain}")
        if max_col_steps < 1:
            raise ValueError(f"ParallelPyxelVM: max_col_steps must be >= 1, got {max_col_steps}")
        self.threads = threads
        self.debug = debug
        self.persist_shared = persist_shared
        self.shared_path = shared_path
        self.strict_mode = strict_mode
        self.max_chain = max_chain
        self.max_col_steps = max_col_steps
        self.clock = 0
        # Opcode map: name → (R, G, B). R channel is the dispatch key.
        self._opmap = map if map is not None else MAP
        # Reverse: R value → opcode name, built once at construction.
        self._rev_opmap = {v[0]: k for k, v in self._opmap.items()}
        self.registers = {
            f"T{i}": {"A": 0, "B": 0, "OUT": 0, "T": 0}
            for i in range(threads)
        }
        self.shared = {f"M{i}": 0 for i in range(8)}
        self.mem_map = {}
        self.next_frame = None
        self.frame_registry = {0: "whisperframe_B.png"}

        if persist_shared:
            self._load_shared()

    # ------------------------------------------------------------------
    # Shared memory persistence
    # ------------------------------------------------------------------

    def _load_shared(self):
        if not os.path.exists(self.shared_path):
            return
        try:
            with open(self.shared_path, "r") as f:
                data = json.load(f)
        except Exception as e:
            if self.debug:
                print(f"[WARN] _load_shared: could not read '{self.shared_path}': {e}")
            return
        for k, v in data.items():
            if k not in self.shared:
                continue
            try:
                self.shared[k] = int(v)    # shared memory holds ints only
            except (TypeError, ValueError):
                if self.debug:
                    print(f"[WARN] _load_shared: invalid value for '{k}': {v!r}, using 0")

    def _save_shared(self):
        try:
            with open(self.shared_path, "w") as f:
                json.dump(self.shared, f)
        except Exception as e:
            print(f"[WARN] _save_shared: failed to write '{self.shared_path}': {e}")

    # ------------------------------------------------------------------
    # Memory naming layer
    # ------------------------------------------------------------------

    def set_mem_map(self, mapping: dict):
        """Map human names to shared memory slots. Example: {"score": "M0"}"""
        self.mem_map = mapping

    def register_frame(self, index: int, path: str):
        """Add or update a frame_registry entry. index=0 is "whisperframe_B.png" by default."""
        self.frame_registry[index] = path

    # ------------------------------------------------------------------
    # Internal debug helpers
    # ------------------------------------------------------------------

    def _debug_diff(self, y: int, x: int, op: str, pre_thread: dict, pre_shared: dict, thread: dict):
        """Print only the registers and shared slots that changed (Task 2)."""
        diff = []
        for reg in ("A", "B", "OUT"):                      # T excluded — changes every step
            if thread[reg] != pre_thread[reg]:
                diff.append(f"{reg}: {pre_thread[reg]} → {thread[reg]}")
        for slot, val in self.shared.items():
            if val != pre_shared[slot]:
                diff.append(f"{slot}: {pre_shared[slot]} → {val}")
        if diff:
            print(f"[T{y} x={x}] {op} → {', '.join(diff)}")
        else:
            print(f"[T{y} x={x}] {op}")

    # ------------------------------------------------------------------
    # Internal: execute a single frame, return next_frame path or None
    # ------------------------------------------------------------------

    def _execute_single(self, image_path: str):
        img = Image.open(image_path).convert("RGB")
        arr = np.array(img, dtype=np.uint8)
        width = arr.shape[1]
        height = arr.shape[0]

        self.next_frame = None
        halted = set()
        skip_next = {y: False for y in range(self.threads)}
        next_frame = None

        x = 0
        _step = 0
        while x < width and _step < self.max_col_steps:
            _step += 1
            self.clock = x
            jump_to = None                               # reset each column; first JMP wins

            for y in range(self.threads):
                if y >= height or y in halted:
                    continue

                # Task 1 — skip consumption log
                if skip_next[y]:
                    skip_next[y] = False
                    if self.debug:
                        print(f"[T{y}] SKIPPING column {x}")
                    continue

                thread = self.registers[f"T{y}"]
                thread["T"] = x
                r, g, b = int(arr[y, x, 0]), int(arr[y, x, 1]), int(arr[y, x, 2])

                # Task 2 — pre-snapshot for register diff
                if self.debug:
                    pre_thread = dict(thread)
                    pre_shared = dict(self.shared)

                # Resolve opcode name from current map's reverse lookup.
                # R=0 is always NOP regardless of map (reserved).
                opcode = self._rev_opmap.get(r)
                op = opcode if opcode else f"UNK({r})"

                if opcode == "HALT":                                 # G/B ignored
                    halted.add(y)
                    if self.debug:
                        print(f"[T{y} x={x}] HALT")

                elif opcode == "SAVE_STATE":                         # G = frame_index
                    frame_index = g
                    if frame_index in self.frame_registry:
                        next_frame = self.frame_registry[frame_index]
                        self.next_frame = next_frame
                        print(f"[SAVE_STATE] Daisy-chain trigger → next frame: {next_frame}")
                        if self.debug:
                            print(f"[T{y} x={x}] SAVE_STATE idx={frame_index} → {next_frame}")
                    else:
                        print(f"[WARN] SAVE_STATE frame index {frame_index} not in registry at x={x} T{y}")

                elif opcode == "VERIFY_FREQ":                        # G/B reserved
                    mirror_x = width - 1 - x
                    mr, mg, mb = (int(arr[y, mirror_x, 0]),
                                  int(arr[y, mirror_x, 1]),
                                  int(arr[y, mirror_x, 2]))
                    if (r, g, b) == (mr, mg, mb):
                        print(f"[VERIFY_FREQ] Frequency auth success for thread T{y}")
                        thread["A"] = 1
                    else:
                        thread["A"] = 0
                    if self.debug:
                        self._debug_diff(y, x, op, pre_thread, pre_shared, thread)

                elif opcode == "SET_REG":                            # A = B channel
                    thread["A"] = b
                    if self.debug:
                        self._debug_diff(y, x, op, pre_thread, pre_shared, thread)

                elif opcode == "SET_B":                              # B = B channel
                    thread["B"] = b
                    if self.debug:
                        self._debug_diff(y, x, op, pre_thread, pre_shared, thread)

                elif opcode == "ADD_THREAD":                         # OUT = (A+B) & 0xFF
                    thread["OUT"] = (thread["A"] + thread["B"]) & 0xFF
                    if self.debug:
                        self._debug_diff(y, x, op, pre_thread, pre_shared, thread)

                elif opcode == "IF_A_EQ":                            # skip next if A != b
                    if thread["A"] != b:
                        skip_next[y] = True
                        if self.debug:
                            print(f"[T{y}] IF_A_EQ {b} → SKIP")
                    else:
                        if self.debug:
                            print(f"[T{y}] IF_A_EQ {b} → PASS")

                elif opcode == "LOAD_MEM":                           # A = shared[M{g}]
                    key = f"M{g}"
                    if key in self.shared:
                        thread["A"] = self.shared[key]
                        if self.debug:
                            self._debug_diff(y, x, op, pre_thread, pre_shared, thread)
                    else:
                        if self.strict_mode:
                            raise ValueError(f"LOAD_MEM invalid address M{g} at x={x} T{y}")
                        if self.debug:
                            print(f"[WARN] LOAD_MEM invalid address M{g} at x={x} T{y}")

                elif opcode == "STORE_MEM":                          # shared[M{g}] = A
                    key = f"M{g}"
                    if key in self.shared:
                        self.shared[key] = thread["A"]
                        if self.debug:
                            self._debug_diff(y, x, op, pre_thread, pre_shared, thread)
                    else:
                        if self.strict_mode:
                            raise ValueError(f"STORE_MEM invalid address M{g} at x={x} T{y}")
                        if self.debug:
                            print(f"[WARN] STORE_MEM invalid address M{g} at x={x} T{y}")

                elif opcode == "NOT":                                # A = (~A) & 0xFF
                    thread["A"] = (~thread["A"]) & 0xFF
                    if self.debug:
                        self._debug_diff(y, x, op, pre_thread, pre_shared, thread)

                elif opcode == "SUB":                                # A = (A - B) & 0xFF
                    thread["A"] = (thread["A"] - thread["B"]) & 0xFF
                    if self.debug:
                        self._debug_diff(y, x, op, pre_thread, pre_shared, thread)

                elif opcode == "MUL":                                # A = (A * B) & 0xFF
                    thread["A"] = (thread["A"] * thread["B"]) & 0xFF
                    if self.debug:
                        self._debug_diff(y, x, op, pre_thread, pre_shared, thread)

                elif opcode == "DIV":                                # A = A // B  (0 if B==0)
                    thread["A"] = thread["A"] // thread["B"] if thread["B"] != 0 else 0
                    if self.debug:
                        self._debug_diff(y, x, op, pre_thread, pre_shared, thread)

                elif opcode == "CMP":                                # OUT = 1 if A==B else 0
                    thread["OUT"] = 1 if thread["A"] == thread["B"] else 0
                    if self.debug:
                        self._debug_diff(y, x, op, pre_thread, pre_shared, thread)

                elif opcode == "COPY_OUT":                           # A = OUT
                    thread["A"] = thread["OUT"]
                    if self.debug:
                        self._debug_diff(y, x, op, pre_thread, pre_shared, thread)

                elif opcode == "JMP":                                # jump to column G; first thread wins
                    if jump_to is None:
                        target = g
                        if 0 <= target < width:
                            jump_to = target
                            if self.debug:
                                print(f"[T{y} x={x}] JMP → col {target}")
                        elif self.debug:
                            print(f"[T{y} x={x}] JMP {target} out of range, no-op")

                else:
                    if r != 0:                                       # R=0 is always NOP
                        if self.strict_mode:
                            raise ValueError(f"Unknown opcode R={r} G={g} B={b} at x={x} T{y}")
                        if self.debug:
                            print(f"[WARN] Unknown opcode R={r} G={g} B={b} at x={x} T{y}")

            # Advance column pointer; JMP overrides normal +1 progression
            if jump_to is not None:
                x = jump_to
            else:
                x += 1

        if _step >= self.max_col_steps and x < width:
            raise RuntimeError(
                f"max_col_steps ({self.max_col_steps}) reached in frame "
                f"'{image_path}' at column {x}/{width}. "
                f"Likely a JMP loop with no exit; raise max_col_steps on the VM "
                f"constructor if this is intentional."
            )

        return next_frame

    # ------------------------------------------------------------------
    # Public: chain frames up to self.max_chain deep
    # ------------------------------------------------------------------

    def execute_texture(self, image_path: str) -> dict:
        current = image_path
        chained = False
        for i in range(self.max_chain):
            next_frame = self._execute_single(current)
            if next_frame is None:
                chained = False
                break
            if not os.path.exists(next_frame):
                raise FileNotFoundError(
                    f"SAVE_STATE chained to '{next_frame}' but the file "
                    f"does not exist. Check frame_registry entries."
                )
            current = next_frame
            chained = True
        else:
            # for/else: loop exhausted without a break — chain hit the cap.
            if chained:
                raise RuntimeError(
                    f"max_chain ({self.max_chain}) reached while following "
                    f"SAVE_STATE daisy-chain. Last frame: '{current}'. "
                    f"Raise max_chain on the VM constructor if this is intentional."
                )

        if self.persist_shared:
            self._save_shared()

        return self.registers

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def dump_state(self):
        rev_map = {v: k for k, v in self.mem_map.items()}
        print("=== Registers ===")
        for tid, regs in self.registers.items():
            print(f"  {tid}: {regs}")
        print(f"  clock: {self.clock}")
        print("=== Shared Memory ===")
        for key, val in self.shared.items():
            label = rev_map.get(key, "")
            suffix = f" ({label})" if label else ""
            print(f"  {key}{suffix}: {val}")


# ---------------------------------------------------------------------------
# ThoughtCompiler
# ---------------------------------------------------------------------------

class ThoughtCompiler:
    def __init__(self, mapkey: dict = None, map: dict = None):
        # 'map' takes precedence; 'mapkey' for backward compatibility; fallback to global MAP.
        effective = map if map is not None else mapkey
        self.mapkey = effective if effective is not None else MAP

    def compile_thought_matrix(self, parallel_instructions: list, out_path: str):
        num_threads = len(parallel_instructions)
        num_cols = max((len(row) for row in parallel_instructions), default=0)

        if num_threads == 0 or num_cols == 0:
            print("[ThoughtCompiler] Empty program — nothing to compile")
            return

        img = Image.new("RGB", (num_cols, num_threads), (0, 0, 0))
        pixels = img.load()

        for y, thread_instrs in enumerate(parallel_instructions):
            for x, instr in enumerate(thread_instrs):
                pixels[x, y] = self.mapkey.get(instr, (0, 0, 0))

        img.save(out_path)
        print(f"[ThoughtCompiler] Saved {num_threads}x{num_cols} thread matrix → {out_path}")


# ---------------------------------------------------------------------------
# SimpleCompiler
# ---------------------------------------------------------------------------

class SimpleCompiler:
    """
    Converts a human-readable instruction matrix into an executable PNG.

    Input format:
        [
            ["SET_A 10", "SET_B 5", "ADD", "STORE_MEM 0"],
            ["LOAD_MEM 0", "IF_A_EQ 15", "SET_A 1", "HALT"],
        ]

    Each inner list is a thread (row). Each string is one instruction (column).
    Rows are padded with NOPs to equal length.

    Synthetic instructions (compiler-only, expanded before emit):
        OUT_TO_A  — Moves the result of the previous ADD/CMP into A so it can
                    be used by subsequent instructions (notably STORE_MEM, which
                    writes A). Expands to the single real opcode COPY_OUT.
                    Retained as an alias for readability in hand-written programs.
    """

    # Maps instruction string → (opcode_map_key, G_builder, B_builder).
    # R is resolved at parse time from the instance's opcode map, making
    # the compiler fully map-aware without hardcoding any R value here.
    _INSTR_SPECS = {
        "SET_A":       ("SET_REG",     lambda a: 0,                      lambda a: int(a[0])),
        "SET_B":       ("SET_B",       lambda a: 0,                      lambda a: int(a[0])),
        "ADD":         ("ADD_THREAD",  lambda a: 0,                      lambda a: 0),
        "IF_A_EQ":     ("IF_A_EQ",     lambda a: 0,                      lambda a: int(a[0])),
        "LOAD_MEM":    ("LOAD_MEM",    lambda a: int(a[0]),              lambda a: 0),
        "STORE_MEM":   ("STORE_MEM",   lambda a: int(a[0]),              lambda a: 0),
        "VERIFY_FREQ": ("VERIFY_FREQ", lambda a: 0,                      lambda a: 0),
        "SAVE_STATE":  ("SAVE_STATE",  lambda a: int(a[0]) if a else 0,  lambda a: 0),
        "HALT":        ("HALT",        lambda a: 0,                      lambda a: 0),
        # Extended opcode set
        "NOT":         ("NOT",         lambda a: 0,                      lambda a: 0),
        "SUB":         ("SUB",         lambda a: 0,                      lambda a: 0),
        "MUL":         ("MUL",         lambda a: 0,                      lambda a: 0),
        "DIV":         ("DIV",         lambda a: 0,                      lambda a: 0),
        "CMP":         ("CMP",         lambda a: 0,                      lambda a: 0),
        "JMP":         ("JMP",         lambda a: int(a[0]),              lambda a: 0),
        "COPY_OUT":    ("COPY_OUT",    lambda a: 0,                      lambda a: 0),
    }

    # Synthetic instructions expand to one or more primitive instructions.
    _SYNTHETIC = {
        "OUT_TO_A": ["COPY_OUT"],
    }

    def __init__(self, map: dict = None):
        # Opcode map used for R-value lookup during compilation.
        self._map = map if map is not None else MAP

    def _expand(self, program: list) -> list:
        """Replace synthetic instructions with their primitive expansions."""
        expanded = []
        for thread in program:
            row = []
            for instr in thread:
                op = instr.strip().split()[0].upper() if instr.strip() else ""
                if op in self._SYNTHETIC:
                    row.extend(self._SYNTHETIC[op])
                else:
                    row.append(instr)
            expanded.append(row)
        return expanded

    def _validate(self, program: list, debug: bool):
        """
        Warn about common instruction logic mistakes (Task 6).
        Only fires when debug=True. Never raises — warnings only.
        """
        if not debug:
            return
        for y, thread in enumerate(program):
            ops = []
            for instr in thread:
                parts = instr.strip().split()
                ops.append(parts[0].upper() if parts else "")

            if not ops:
                continue

            # Warn if IF_A_EQ is the last instruction (nothing to guard)
            if ops[-1] == "IF_A_EQ":
                print(f"[WARN] Compiler: T{y} IF_A_EQ is last instruction — no effect (nothing to skip/execute)")

            # Scan for ADD → STORE_MEM without A being modified in between.
            # All ops that write register A are listed here.
            _a_writers = {"SET_A", "LOAD_MEM", "VERIFY_FREQ", "NOT", "SUB", "MUL", "DIV", "COPY_OUT"}
            for i, op in enumerate(ops):
                if op != "ADD":
                    continue
                a_modified = False
                stored_after = False
                for j in range(i + 1, len(ops)):
                    if ops[j] in _a_writers:
                        a_modified = True
                    if ops[j] == "STORE_MEM":
                        stored_after = True
                        if not a_modified:
                            print(
                                f"[WARN] Compiler: T{y} STORE_MEM at position {j} follows ADD "
                                f"without modifying A — STORE_MEM writes A (not OUT). "
                                f"OUT={{}}, A still holds its pre-ADD value."
                            )
                        break
                if not stored_after:
                    print(f"[WARN] Compiler: T{y} ADD at position {i} — OUT result is never stored to shared memory")

    def _parse_one(self, instr: str, debug: bool = False) -> tuple:
        parts = instr.strip().split()
        if not parts:
            return _NOP
        op = parts[0].upper()
        args = parts[1:]
        spec = self._INSTR_SPECS.get(op)
        if spec is None:
            if debug:
                print(f"[WARN] SimpleCompiler: unknown instruction '{instr}'")
            return _NOP
        opcode_key, g_fn, b_fn = spec
        if opcode_key not in self._map:
            if debug:
                print(f"[WARN] SimpleCompiler: opcode '{opcode_key}' not in map")
            return _NOP
        r = self._map[opcode_key][0]
        try:
            g = max(0, min(255, g_fn(args)))
            b = max(0, min(255, b_fn(args)))
            return (r, g, b)
        except (IndexError, ValueError):
            if debug:
                print(f"[WARN] SimpleCompiler: invalid args in '{instr}'")
            return _NOP

    def compile(self, program: list, out_path: str, debug: bool = False) -> str:
        """Compile program matrix to PNG. Returns out_path."""
        program = self._expand(program)
        self._validate(program, debug)

        num_threads = len(program)
        num_cols = max((len(row) for row in program), default=0)

        if num_threads == 0 or num_cols == 0:
            print("[SimpleCompiler] Empty program — nothing to compile")
            return out_path

        img = Image.new("RGB", (num_cols, num_threads), (0, 0, 0))
        px = img.load()

        for y, thread_instrs in enumerate(program):
            for x, instr in enumerate(thread_instrs):
                px[x, y] = self._parse_one(instr, debug=debug)

        img.save(out_path)
        print(f"[SimpleCompiler] Saved {num_threads}x{num_cols} program → {out_path}")
        return out_path


# ---------------------------------------------------------------------------
# Program pattern helpers
# ---------------------------------------------------------------------------

def build_compute_and_store(a: int, b: int, mem_idx: int) -> list:
    """
    One-thread program: set A, set B, add, store A to shared memory.
    NOTE: STORE_MEM writes A (not OUT). After ADD, A still holds its pre-ADD
    value. Use OUT_TO_A (SimpleCompiler synthetic) first if you need OUT in memory.
    Returns an instruction matrix for SimpleCompiler.
    """
    return [[f"SET_A {a}", f"SET_B {b}", "ADD", f"STORE_MEM {mem_idx}"]]


def build_conditional_write(mem_idx: int, compare_val: int) -> list:
    """
    One-thread program: load from shared memory, check value, write A=1 if match.
    IF_A_EQ semantics: if A == compare_val, execute next (SET_A 1).
                       if A != compare_val, skip next (A unchanged).
    Returns an instruction matrix for SimpleCompiler.
    """
    return [[f"LOAD_MEM {mem_idx}", f"IF_A_EQ {compare_val}", "SET_A 1", "HALT"]]


def build_validation_pattern() -> list:
    """
    One-thread program: run VERIFY_FREQ, branch on result, halt.
    VERIFY_FREQ sets A=1 on symmetric pixel match, A=0 otherwise.
    IF_A_EQ 1: if A==1 (match), execute HALT normally.
               if A==0 (no match), skip HALT — thread continues past it.
    Returns an instruction matrix for SimpleCompiler.
    """
    return [["VERIFY_FREQ", "IF_A_EQ 1", "HALT"]]


# ---------------------------------------------------------------------------
# Assertion utility (Task 5)
# ---------------------------------------------------------------------------

def assert_vm_state(vm: ParallelPyxelVM, expected: dict):
    """
    Assert that the VM's state matches expected values. Partial matching is
    supported — only the keys provided in expected are checked.

    Expected dict keys:
        "T0", "T1", …  — dict of register name → value (partial match OK)
        "shared"        — dict of slot name → value  (e.g. {"M0": 10})
        "next_frame"    — str | None

    Raises AssertionError with a readable diff on any mismatch.

    Example:
        assert_vm_state(vm, {
            "T0": {"A": 10, "OUT": 15},
            "shared": {"M0": 10},
            "next_frame": None,
        })
    """
    errors = []

    for key, expected_val in expected.items():
        if key == "shared":
            for slot, want in expected_val.items():
                got = vm.shared.get(slot)
                if got != want:
                    errors.append(f"shared.{slot}: expected {want!r}, got {got!r}")

        elif key == "next_frame":
            if vm.next_frame != expected_val:
                errors.append(f"next_frame: expected {expected_val!r}, got {vm.next_frame!r}")

        elif key.startswith("T") and key[1:].isdigit():
            regs = vm.registers.get(key)
            if regs is None:
                errors.append(f"{key}: thread not found in VM (threads={vm.threads})")
                continue
            for reg, want in expected_val.items():
                got = regs.get(reg)
                if got != want:
                    errors.append(f"{key}.{reg}: expected {want!r}, got {got!r}")

        else:
            errors.append(f"Unknown expected key: {key!r}")

    if errors:
        raise AssertionError(
            "VM state mismatch:\n" + "\n".join(f"  - {e}" for e in errors)
        )


# ---------------------------------------------------------------------------
# VisualMemoryManager
# ---------------------------------------------------------------------------

class VisualMemoryManager:
    def __init__(self, brain_dir: str = "brain_textures/"):
        self.brain_dir = brain_dir
        self.memory_metadata: dict = {}

    def register_frame(self, name: str, score: int):
        self.memory_metadata[name] = {"score": score, "turns": 0}

    def update_decay(self):
        to_prune = []
        for name, meta in self.memory_metadata.items():
            effective_score = meta["score"] / math.sqrt(meta["turns"] + 1)
            if effective_score < 0.5:
                to_prune.append(name)
            else:
                meta["turns"] += 1
        for name in to_prune:
            self.prune_frame(name)

    def prune_frame(self, frame_name: str):
        path = os.path.join(self.brain_dir, frame_name)
        if os.path.exists(path):
            os.remove(path)
        self.memory_metadata.pop(frame_name, None)
        print(f"[VisualMemoryManager] Pruned frame: {frame_name}")


# ---------------------------------------------------------------------------
# FrequencyChallenge
# ---------------------------------------------------------------------------

class FrequencyChallenge:
    def __init__(self, key_row: int = 7):
        self.key_row = key_row

    def generate_challenge_frame(self, base_path: str) -> int:
        img = Image.open(base_path).convert("RGB")
        pixels = img.load()
        width, height = img.size
        secret_freq = random.randint(100, 200)

        if self.key_row < height:
            for x in range(width):
                pixels[x, self.key_row] = (secret_freq, 0, x % 255)

        img.save("challenge_frame.png")
        return secret_freq


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Parallel Pyxel VM")
    parser.add_argument(
        "--mode",
        choices=["execute", "compile", "decay", "challenge", "compile_program"],
        required=True,
    )
    parser.add_argument("--input", dest="input_path")
    parser.add_argument("--output", dest="output_path", default="program.png")
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--persist-shared", action="store_true")
    parser.add_argument("--shared-path", default="shared_state.json")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--program", default=None,
                        help="JSON string: list[list[str]] for compile_program mode")
    parser.add_argument("--program-file", dest="program_file", default=None,
                        help="Path to a JSON file containing list[list[str]] for compile_program mode")
    parser.add_argument("--frame-registry", default=None,
                        help='JSON string mapping frame index → path, e.g. \'{"1":"b.png"}\'')
    args = parser.parse_args()

    if args.mode == "execute":
        if not args.input_path:
            parser.error("--input is required for execute mode")
        vm = ParallelPyxelVM(
            threads=args.threads,
            debug=args.debug,
            persist_shared=args.persist_shared,
            shared_path=args.shared_path,
            strict_mode=args.strict,
        )
        if args.frame_registry:
            try:
                for idx_str, path in json.loads(args.frame_registry).items():
                    vm.register_frame(int(idx_str), path)
            except (json.JSONDecodeError, ValueError) as e:
                parser.error(f"--frame-registry is not valid JSON: {e}")
        vm.execute_texture(args.input_path)
        vm.dump_state()

    elif args.mode == "compile":
        print(f"[compile] Thread matrix shape: ({args.threads}, <columns>) — wire in later")

    elif args.mode == "decay":
        vmm = VisualMemoryManager()
        if os.path.isdir(vmm.brain_dir):
            for fname in sorted(os.listdir(vmm.brain_dir)):
                if fname.endswith(".png"):
                    vmm.register_frame(fname, score=10)
        vmm.update_decay()

    elif args.mode == "challenge":
        if not args.input_path:
            parser.error("--input is required for challenge mode")
        fc = FrequencyChallenge()
        freq = fc.generate_challenge_frame(args.input_path)
        print(f"[FrequencyChallenge] Secret frequency: {freq}")

    elif args.mode == "compile_program":
        if args.program and args.program_file:
            parser.error("--program and --program-file are mutually exclusive")
        if not args.program and not args.program_file:
            parser.error("compile_program mode requires --program or --program-file")
        try:
            if args.program_file:
                with open(args.program_file, "r", encoding="utf-8") as _pf:
                    program = json.load(_pf)
            else:
                program = json.loads(args.program)
        except (OSError, json.JSONDecodeError) as e:
            parser.error(f"program could not be loaded: {e}")
        compiler = SimpleCompiler()
        compiler.compile(program, out_path=args.output_path, debug=args.debug)
