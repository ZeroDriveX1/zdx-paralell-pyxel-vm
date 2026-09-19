# ZDX Parallel Pyxel VM

ZDX is a deterministic, pixel-native virtual machine. Programs are encoded as PNG images: each image row is a logical thread, each column is a time step, and each pixel maps to one instruction.

The repository also includes a lightweight agent runtime, PNG-backed agent memory, and experimental node-coordination components.

## Project status

| Component | Status | Notes |
| --- | --- | --- |
| VM and compiler | Working and tested | Deterministic multi-thread execution with a fixed 16-opcode map |
| Agent runtime and registry | Working and tested | Explicit dependency registration; VM state can be persisted after a run |
| Pixel memory | Transactional and tested | Atomic checksummed PNG records and a versioned, process-locked filename index |
| Node protocol and TCP coordinator | Authenticated foundation | Canonical signed envelope and mutual session authentication are integrated; production deployment validation remains |
| Authentication and Ed25519 modules | Working and tested | Ed25519 identity, pinned trust, session expiry, nonces, strict sequences, timestamps, and checksums are verified end to end |
| Scheduler and simulation | Working foundation | Deterministic capability-aware selection; not a production distributed scheduler |
| Android node | Prototype source only | No complete Gradle application or supported release build |

> Security warning: production networking requires mutual TLS and signed application envelopes. Keep model endpoints local or behind equivalent authenticated transport, and do not place secrets in mission prompts or memory. PNG encoding is storage, not encryption. See [SECURITY_MODEL.md](SECURITY_MODEL.md).

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

All persistent JSON state uses the canonical `StateStore` envelope with schema and envelope versions, creation/update timestamps, compatibility metadata, a SHA-256 checksum, and migration history. Commits use same-directory temporary files, file and directory `fsync`, atomic replacement, a previous-generation backup, and process locks. Invalid state is quarantined and recovered from its verified backup when possible. Legacy unversioned JSON and pixel-memory PNG values remain readable and are upgraded on the next managed write/load.

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

### Authenticated node tools

The coordinator and client require mutual TLS plus explicit application public-key pinning. A trust argument has the form `NODE_ID=PUBLIC_KEY_FILE`, where the file contains the 32-byte raw Ed25519 public key:

```bash
python zdx_cli.py serve --port 8765 --tls-ca ca.pem --tls-cert coordinator.pem --tls-key coordinator-key.pem --trust NODE_ID=node-public-key.raw
python zdx_cli.py ping 127.0.0.1 --port 8765 \
  --coordinator-id COORDINATOR_ID --coordinator-public-key coordinator-public-key.raw \
  --tls-ca ca.pem --tls-cert worker.pem --tls-key worker-key.pem --server-hostname localhost
python zdx_cli.py hash program.png
```

The server exchanges length-prefixed canonical JSON messages and does not execute received programs. Every accepted application message is signed and bound to a mutually authenticated, expiring session. Enrollment is operator-managed key pinning. TLS 1.3 is required by default, including peer certificates and hostname verification; production connection quotas are not implemented.

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

### Operational validation

`zdx_validation.py` provides deterministic bounded soak, persistence stress, resource monitoring, and machine-readable microbenchmarks:

```bash
python zdx_validation.py --mode soak --iterations 100 --output soak.json
python zdx_validation.py --mode stress --iterations 1000 --output stress.json
python zdx_validation.py --mode benchmarks --iterations 100 --output benchmarks.json
# Framework for an operator-controlled 24-hour run; not executed in Pass 13:
python zdx_validation.py --mode soak --duration 86400 --output soak-24h.json
```

Pass 13 locally validated 100 integrated soak cycles in 24.87 seconds, including 1,033 persistence commits, with zero descriptor/thread growth, zero stale temporary transactions, stable lock/backup counts after warmup, and 1.71 MB traced net memory growth (2.10 MB peak). A separate 1,000-commit stress completed in 20.05 seconds at 49.86 commits/second with one bounded backup and no temporary files. These are bounded results on the current Termux host, not 24-hour, physical multi-machine, or distributed-filesystem validation. Raw results are under `validation_results/`.

## Repository map

- `zdx_parallel_vm.py` — compiler, VM, CLI, and visual-memory helpers
- `zdx_pixel_memory/` — PNG codec, key/value store, and agent-memory API
- `zdx_storage.py`, `zdx_migrations/` — transactional storage, locking, recovery, and schema migrations
- `zdx_metrics.py`, `zdx_failure.py`, `zdx_validation.py` — metrics, deterministic failure injection, soak tests, and benchmarks
- `pyxel_registry.py` — explicit session-scoped component registry
- `zdx_agent_runtime.py` — VM-to-agent-memory coordinator
- `zdx_network.py`, `zdx_server.py`, `zdx_node/` — experimental coordination layer
- `zdx_auth_pipeline.py`, `zdx_ed25519_signer.py` — authentication foundations
- `zdx_scheduler.py`, `zdx_simulator.py` — deterministic node selection and simulation
- `android/` — Android data models and prototype node source
- `logs/` — hi  The /logs directory documents the iterative development of this repository. It contains the engineering history from the Codex implementation passes that produced the current codebase. Some log entries describe work in progress at the time they were written; the repository should be considered authoritative for the current implementation.

## Known limitations

- Mutual TLS and application authentication are implemented, but automated discovery, dynamic enrollment, durable certificate rotation orchestration, connection-level DoS controls, and physical multi-machine validation remain.
- PNG memory files are not encrypted and offer no sender authentication.
- Legacy experimental modules remain as compatibility surfaces; `zdx_network`, `zdx_session`, `security.identity`, and `zdx_node_registry` are the canonical active networking, authentication, identity, and coordinator implementations.
- Android sources are not a complete installable application.
- Process locks use POSIX `fcntl`; semantics on network/distributed filesystems require deployment-specific validation.
- The 24-hour soak framework exists, but Pass 13 executed only a bounded 100-cycle soak; sustained multi-day and physical multi-node validation remains.

For project direction, see [ROADMAP.md](ROADMAP.md), [TODO.md](TODO.md), and [ARCHITECTURE.md](ARCHITECTURE.md).

## License

MIT. See [LICENSE](LICENSE).

## Local Ollama missions

Install Ollama, pull the development model, and start its local service:

```bash
ollama pull qwen2.5:1.5b
ollama serve
```

All provider settings come from environment variables. Defaults target `http://localhost:11434` and `qwen2.5:1.5b`:

```bash
export ZDX_OLLAMA_ENDPOINT=http://localhost:11434
export ZDX_OLLAMA_MODEL=qwen2.5:1.5b
export ZDX_OLLAMA_TIMEOUT_SECONDS=30
export ZDX_OLLAMA_MAX_RETRIES=2
export ZDX_OLLAMA_BACKOFF_SECONDS=0.25
export ZDX_OLLAMA_JITTER_SECONDS=0.1
export ZDX_OLLAMA_POOL_SIZE=4
export ZDX_OLLAMA_NUM_PREDICT=256
export ZDX_OLLAMA_TEMPERATURE=0.2
export ZDX_OLLAMA_MAX_RESPONSE_BYTES=4194304
export ZDX_MISSION_HISTORY=.zdx/missions.json
export ZDX_REFLECTION_ENABLED=false
export ZDX_REFLECTION_MAX_CYCLES=2
```

Run one mission through the required scheduler and agent path:

```bash
python zdx_mission_cli.py "Explain the VM execution order in three sentences."
```

The execution flow is Mission → `ZDXScheduler` → `MissionAgent` → `InferenceProvider` → verification → transactional `MissionHistory` → metrics. Agents receive an `InferenceProvider`; they do not construct or call Ollama HTTP clients directly. Mission records contain the mission, prompt, model, timestamp, latency, token estimates, verification status, and response. Optional `ZDXAgentMemory` mirroring writes the same record through PNG memory when supplied by the embedding application.

Reflection is disabled by default. When enabled, a failed verification triggers critique and revision, stops immediately after a passing revision, and never exceeds two cycles. The built-in verifier only rejects empty output; deployments should inject a mission-specific verifier for stronger correctness or safety guarantees.
