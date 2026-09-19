# ZDX Development Status

## Production networking pass 11.0

Implemented and locally verified: one canonical signed envelope/version; key-derived Ed25519 identities and trust pinning; mutual challenge authentication; expiring sessions, reconnect replacement, nonce defense, strict sequences, timestamps and checksums; authenticated TCP integration; hardened registration, heartbeat binding, duplicate detection and stale cleanup; and a versioned key-rotation authorization abstraction.

Not yet production-validated: TLS confidentiality, durable trust/revocation/session recovery policy, listener connection quotas, operational rate controls, and multiple physical machines under adverse network conditions.

Android work, PNG encryption, and persistence migrations were intentionally not changed.

## Production persistence pass 12.0

Implemented and locally verified: canonical atomic byte commits; file/directory fsync; previous-generation rollback; corruption quarantine and verified backup recovery; bounded POSIX reader/writer locks with crash-released stale-lock handling; checksummed schema envelopes; legacy version detection; sequential migration registration, discovery, logging, and failure rollback; and transactional persistence for VM shared state, pixel memory/indexes, coordinator state, identities/key metadata, karma, trusted peers, revocations, session metadata, and node registry metadata.

Private Ed25519 keys remain necessary secret files with mode `0600`; public/key metadata is stored separately in versioned state. Recovered session records are audit metadata only—sessions require fresh authentication after restart.

Not validated here: lock and durability behavior on NFS or other distributed filesystems, power-loss behavior of specific device filesystems, and multi-machine shared-storage deployments. Android work and PNG encryption remain out of scope.

## Production validation pass 13.0

Locally executed and passed: 156 automated tests; a 100-cycle integrated VM/scheduler/persistence/pixel/session/registry/coordinator/migration soak; 1,000 sequential atomic commits; deterministic failure recovery at three commit phases and registry persistence; 500 malformed packet cases; 100 randomized state records; malformed migration, PNG metadata, transaction, and identity cases; adverse packet duplication, delay, reordering, reconnect, expiry, and registry rebuild scenarios; and nine repeatable microbenchmarks.

Measured soak resources: zero thread growth, zero descriptor growth, zero stale transactions, stable lock/backup counts after warmup, 1,706,188 bytes traced memory growth, and 2,103,830 bytes peak traced memory. The soak produced 1,033 commits with 12.07 ms average and 42.60 ms maximum measured commit latency. These measurements apply only to the current local Termux environment and bounded workload.

Not validated: a continuous 24-hour execution, physical coordinator/node restart, real packet loss across machines, TLS deployment, power interruption, NFS/distributed locks, Android runtime, or fleet-scale capacity. Production readiness therefore remains conditional rather than general.

## Distributed deployment pass 14.0

Locally verified on 2026-07-30: TLS 1.3 mutual certificate authentication, hostname verification, expired/revoked certificate rejection, live context rotation for new connections, three isolated worker processes joining/leaving/rejoining, scheduler candidate redistribution, coordinator restart with fresh authentication, deterministic stream-fault failure and reconnect, and SIGKILL during commits/migration/registry updates with valid recovery. Fourteen focused tests passed.

RC2 acceptance is not complete. The environment had one physical ARM64 Android/Termux host and no VMs or remote machines. No 24-hour run, real multi-machine discovery, actual distributed workload dispatch, privileged network emulation, physical power removal, Docker/systemd/Windows execution, or capacity-to-failure test was possible. Packaging is prepared for external validation; it is not certified.

## Local Ollama inference pass 15.0

Implemented: environment-only Ollama configuration with Qwen 2.5 1.5B defaults; pooled HTTP(S) connections; bounded timeout/retry/backoff/jitter; structured failures; NDJSON streaming compatibility; explicit connectivity checks; scheduler-mediated mission execution; relevant-memory prompt construction; pluggable verification; transactional mission history; optional PNG memory mirroring; bounded single-agent reflection; existing metrics export; CLI execution; and machine-readable benchmarks.

Deterministic tests use an in-process Ollama-compatible HTTP server and do not require a model download. A real local Ollama service was detected on the Pass 15 Termux host, but Qwen download/live benchmark status is recorded separately and must not be inferred from stub results. The built-in non-empty verifier is structural, not a factual or policy correctness proof. Distributed inference, voting, branching, and BAN routing remain intentionally absent.

Pass 15 validation evidence: 13 focused Ollama/mission tests passed. The finalized 20-iteration deterministic-provider benchmark measured 1,351.60 framework missions/minute, 44.39 ms average end-to-end benchmark time, 28.05 µs scheduler overhead, 37.31 ms persistence overhead, and 27.85 ms for one forced reflection cycle. These figures exclude model inference and are not Ollama throughput.

`qwen2.5:1.5b` (986 MB, ID `65ec06548149`) was installed and verified in the local Ollama inventory. A live scheduled mission correctly failed closed after three HTTP 500 attempts and persisted a valid failure record. Direct `ollama run` confirmed the external Ollama 0.32.3 installation lacks its `llama-server` binary; therefore no real model response or live model benchmark is claimed. The general Linux repair bundle was not retained because it began installing irrelevant CUDA assets and did not provide a practical Termux repair path.
