# ZDX Distributed Testing Plan

## Automated authentication coverage

- valid mutual node/coordinator authentication;
- invalid signatures, stale timestamps, replayed packets, and duplicate nonces;
- strict sequences, unsupported versions/types, and checksum failures;
- malformed JSON/envelopes, session expiry, cleanup, and reconnect replacement;
- duplicate IDs, cross-identity session reuse, stale cleanup, and heartbeat binding;
- coordinator recovery, key-rotation authorization, authenticated TCP registration, and unsigned-packet rejection.

## Existing regression coverage

VM/compiler, agent registry/runtime, frame synchronization/manifests, enrollment foundations, scheduler/simulation, identity persistence, and signing tests remain enabled.

## Automated persistence coverage

- interrupted temporary writes and atomic rollback after commit failure;
- concurrent process writers and concurrent readers;
- lock timeout and stale diagnostic lock recovery;
- checksums, corruption quarantine, and backup restoration;
- legacy state and legacy PNG backward compatibility;
- sequential migration upgrades, migration logs, missing/newer versions, and failed-migration rollback;
- transactional pixel index/value commits;
- identity permissions and metadata separation;
- trusted-peer, revocation, session-audit, registry, and coordinator restart recovery.

## Required production validation

The local suite does not prove physical-machine behavior. Before release, test TLS deployment, packet loss/reordering, clock drift, connection exhaustion controls, rotation/revocation operations, mixed-platform interoperability, power-loss durability, and POSIX locking/fsync semantics on each production or distributed filesystem.

## Pass 13 operational validation

- deterministic bounded soak across VM, scheduler, persistence, pixel memory, sessions, registry, coordinator, and migrations;
- 1,000-commit integrity stress with checksum and bounded-generation checks;
- resource checks for memory, descriptors, threads, locks, backups, retries, and stale transactions;
- deterministic failures after temp fsync, after backup rotation, before final replacement, and before registry persistence;
- reconnect, duplicate, delayed, reordered, expired, heartbeat, coordinator trust restart, and registry rebuild scenarios;
- 500 deterministic packet fuzz cases, 100 state-record cases, and malformed migration/PNG/identity/transaction envelopes;
- repeatable VM, persistence, scheduler, registry, authenticated dispatch, Ed25519, PNG, and migration benchmarks.

The soak harness accepts `--duration 86400`, but Pass 13 ran 100 bounded iterations only. Test reports must not equate bounded success with a completed 24-hour or fleet validation.

## Pass 14 distributed validation

Executed locally:

```bash
python -m pytest -q test_distributed_deployment.py test_tls.py test_network_faults.py test_power_loss.py
```

Expected result: 14 passed. Coverage includes three spawned worker processes, mutual TLS, hostname/expiry/revocation failures, live server certificate rotation, coordinator restart, join/leave/rejoin, scheduler candidate recovery, deterministic encrypted-stream corruption/partition recovery, and SIGKILL during persistence/migration/registry writes.

Mandatory external RC2 gate (not executed here): at least three physical/virtual machines, actual network fault injection including clock skew, 24-hour soak, physical/hypervisor power loss, increasing capacity steps to an observed limit, and Docker/systemd/Windows service runs. Attach commands, topology, host versions, raw JSON metrics, and artifacts. A local process or loopback test must not be reported as multi-machine acceptance.

## Pass 15 Ollama and mission validation

Deterministic coverage uses a local Ollama-compatible HTTP test server and verifies `/api/tags` connectivity, default/environment configuration, connection reuse, normal generation, NDJSON responses, timeouts, retry/backoff, malformed data, structured provider failures, scheduler-mediated missions, transactional persistence, optional PNG mirroring, relevant-memory injection, metrics, early reflection termination, the two-cycle ceiling, and scheduler recovery after provider failure.

```bash
python -m pytest -q test_ollama_integration.py
python zdx_ollama_benchmark.py --iterations 20 --output validation_results/pass15_ollama_stub_benchmarks.json
# Only after the configured model is installed:
python zdx_ollama_benchmark.py --live --iterations 3 --output validation_results/pass15_ollama_live_benchmarks.json
```

Stub-provider throughput measures framework overhead and must not be presented as model throughput. Live results are specific to the model, quantization, host, temperature/runtime state, and prompt. Provider unavailability must fail with a structured error, persist a secret-free failure record, release scheduler control, and leave storage valid.

Local live attempt result: the Qwen model was installed and `/api/tags` inventory succeeded, but generation was blocked by the external Ollama installation's missing `llama-server`. The provider retried three times, returned a structured `http_error`, scheduler execution unwound, and transactional failure history remained readable. This validates graceful unavailability, not live inference correctness or throughput.
