# ZDX Parallel Pyxel VM

ZDX is a deterministic, pixel-native virtual machine. Programs are encoded as PNG images: each image row is a logical thread, each column is a time step, and each pixel maps to one instruction.

The repository also includes a lightweight agent runtime, PNG-backed agent memory, and experimental node-coordination components.

## Project status

| Component | Status | Notes |
| --- | --- | --- |
| VM and compiler | Working and tested | Deterministic multi-thread execution with a fixed 16-opcode map |
| Agent runtime and registry | Working and tested | Explicit dependency registration; VM state can be persisted after a run |
| Pixel memory | Working and tested | Stores JSON-compatible values in PNG files with a JSON filename index |
| Node protocol and local TCP server | Experimental | Suitable for local development only; transport is not TLS-authenticated |
| Authentication and Ed25519 modules | Partial | Unit-tested pipeline exists, but it is not integrated end to end with the TCP server |
| Scheduler and simulation | Working foundation | Deterministic capability-aware selection; not a production distributed scheduler |
| Android node | Prototype source only | No complete Gradle application or supported release build |

> Security warning: do not expose the current TCP server to an untrusted network or execute untrusted workloads. PNG encoding is storage, not encryption. See [SECURITY_MODEL.md](SECURITY_MODEL.md).

## Requirements

- Python 3.9 or newer
- NumPy
- Pillow
- Cython and a C compiler only when building native extensions
- `cryptography` only for Ed25519 identity features

Create an isolated environment and install the core dependencies:

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

For development and testing:

```bash
python -m pip install -r requirements-dev.txt
```

Ed25519 support is separated because some Android/Termux environments require a platform package or Rust toolchain:

```bash
python -m pip install -r requirements-security.txt
```

## Quick start

Create a one-thread program in `program.json`:

```json
[
  ["SET_A 10", "SET_B 5", "ADD", "COPY_OUT", "STORE_MEM 0", "HALT"]
]
```

Compile it to a PNG:

```bash
python zdx_parallel_vm.py \
  --mode compile_program \
  --program-file program.json \
  --output program.png
```

Run it:

```bash
python zdx_parallel_vm.py \
  --mode execute \
  --input program.png \
  --threads 1
```

Expected final values include `A = 15`, `OUT = 15`, and shared-memory slot `M0 = 15`.

## Python API

```python
from zdx_parallel_vm import ParallelPyxelVM, SimpleCompiler

program = [[
    "SET_A 10",
    "SET_B 5",
    "ADD",
    "COPY_OUT",
    "STORE_MEM 0",
    "HALT",
]]

SimpleCompiler().compile(program, "program.png")

vm = ParallelPyxelVM(threads=1)
registers = vm.execute_texture("program.png")

print(registers["T0"])
print(vm.shared["M0"])
```

The number of VM threads should match the number of program rows. Missing rows execute as blank pixels, but matching them explicitly makes mistakes easier to spot.

## Agent runtime and memory

`ZDXAgentRuntime` resolves its VM and optional memory backend through `PyxelRegistry`. This keeps the coordinator independent of the concrete VM and storage implementations.

```python
from pyxel_registry import PyxelRegistry
from zdx_agent_runtime import ZDXAgentRuntime
from zdx_parallel_vm import ParallelPyxelVM
from zdx_pixel_memory import ZDXAgentMemory

registry = PyxelRegistry()
registry.register("vm", ParallelPyxelVM(threads=1))
registry.register(
    "memory",
    ZDXAgentMemory(agent_id="demo", base_dir="./zdx_memory/"),
)

runtime = ZDXAgentRuntime(registry)
registers = runtime.run("program.png")

memory = registry.get("memory")
print(registers)
print(memory.recall("shared_state"))
print(memory.recall("register_state"))
```

Run the self-contained example with:

```bash
python zdx_agent_runtime.py
```

Agent memory accepts JSON-compatible values: strings, numbers, booleans, `None`, lists, and dictionaries. Each value is encoded into a `.px.png` file. `keys.json` maps original keys to collision-safe filenames; it is plaintext metadata and should be included in backups.

## Execution model

Execution is column-first:

```text
for each column (time step):
    for each row (thread T0, T1, ...):
        execute one instruction
```

This ordering is deterministic. An earlier thread can update shared memory and a later thread can observe that update during the same column.

The default shared memory has slots `M0` through `M7`. Values are integers. `STORE_MEM` writes register `A`, not `OUT`, so copy arithmetic output into `A` first when needed.

## Instruction set

| Instruction | Effect |
| --- | --- |
| `SET_A n` | Set register `A` to `n` |
| `SET_B n` | Set register `B` to `n` |
| `ADD` | Set `OUT = A + B` |
| `SUB` | Set `A = A - B` |
| `MUL` | Set `A = A * B` |
| `DIV` | Set `A = A // B` |
| `NOT` | Apply bitwise NOT to `A` |
| `CMP` | Set `OUT` to whether `A == B` |
| `COPY_OUT` | Copy `OUT` into `A` |
| `IF_A_EQ n` | Continue if equal; otherwise skip the next instruction |
| `JMP n` | Jump to column `n` |
| `LOAD_MEM n` | Load shared slot `Mn` into `A` |
| `STORE_MEM n` | Store `A` in shared slot `Mn` |
| `VERIFY_FREQ` | Verify the pixel mirror/frequency condition |
| `SAVE_STATE` | Select a registered follow-on frame |
| `HALT` | Stop the current thread |

Use `--strict` during execution to turn unknown opcode colors into errors, and `--debug` to print state changes.

## Command reference

### VM

```bash
python zdx_parallel_vm.py --help
```

Common operations:

```bash
# Compile inline JSON
python zdx_parallel_vm.py --mode compile_program \
  --program '[["SET_A 1", "HALT"]]' \
  --output one.png

# Execute and persist shared memory
python zdx_parallel_vm.py --mode execute \
  --input one.png \
  --threads 1 \
  --persist-shared \
  --shared-path shared_state.json

# Execute with strict validation and tracing
python zdx_parallel_vm.py --mode execute \
  --input one.png \
  --threads 1 \
  --strict \
  --debug
```

### Local node tools

The node CLI is for local experimentation:

```bash
python zdx_cli.py serve --port 8765
python zdx_cli.py ping 127.0.0.1 --port 8765
python zdx_cli.py hash program.png
```

The server exchanges length-prefixed JSON messages and does not execute received programs. It currently lacks integrated TLS, authenticated enrollment, authorization, and production rate limiting.

## Native build

Pure Python is sufficient for development. To compile the VM and pixel-memory modules as native Cython extensions:

```bash
bash build.sh
```

The script creates `dist/` with native modules, type stubs, the registry, and the agent runtime. Native artifacts are specific to the Python version, operating system, and CPU architecture used to build them.

## Testing

Run the complete suite:

```bash
python -m pytest -q
python -m compileall -q .
```

The Ed25519-specific tests skip when `cryptography` is unavailable. A release or security validation must install that dependency and run those tests rather than treating skips as a pass.

Focused suites:

```bash
python -m pytest -q test_vm.py test_registry.py
python -m pytest -q test_network.py test_server.py test_sync.py
python -m pytest -q test_auth_pipeline.py tests/
```

## Repository map

- `zdx_parallel_vm.py` — compiler, VM, CLI, and visual-memory helpers
- `zdx_pixel_memory/` — PNG codec, key/value store, and agent-memory API
- `pyxel_registry.py` — explicit session-scoped component registry
- `zdx_agent_runtime.py` — VM-to-agent-memory coordinator
- `zdx_network.py`, `zdx_server.py`, `zdx_node/` — experimental coordination layer
- `zdx_auth_pipeline.py`, `zdx_ed25519_signer.py` — authentication foundations
- `zdx_scheduler.py`, `zdx_simulator.py` — deterministic node selection and simulation
- `android/` — Android data models and prototype node source
- `logs/` — hi  The /logs directory documents the iterative development of this repository. It contains the engineering history from the Codex implementation passes that produced the current codebase. Some log entries describe work in progress at the time they were written; the repository should be considered authoritative for the current implementation.

## Known limitations

- The network stack is not production-safe or end-to-end authenticated.
- Pixel-memory writes and `keys.json` updates are not transactional or multi-process safe.
- PNG memory files are not encrypted and offer no sender authentication.
- The repository contains overlapping experimental identity, protocol, and registry models.
- Android sources are not a complete installable application.
- Persistent state formats do not yet have a formal migration and recovery system.

For project direction, see [ROADMAP.md](ROADMAP.md), [TODO.md](TODO.md), and [ARCHITECTURE.md](ARCHITECTURE.md).

## License

MIT. See [LICENSE](LICENSE).
