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

## Spatial PNG v1 integration pass 16.0

Implemented on the canonical parallel VM: strict spatial geometry contracts; direct X/Y RGB machine cells; executable/storage plane separation; named storage regions; deterministic typed single-PNG agent memory; resident same-frame agent transactions; canonical spatial metadata propagation through manifests/protocol/sync/compute; and spatial worker capability admission.

The same-frame resident path holds one process lock, decodes one PNG generation, executes the already-decoded raster, updates only the declared non-executable memory region, and performs one atomic PNG checkpoint. Exceptions before commit leave the previous generation unchanged.

Security/correctness hardening includes rejection of non-PNG/non-RGB inputs, boolean/non-integer geometry, inconsistent derived capacities, custom executable named regions, unsupported spatial versions, thread/execution-row mismatches, oversized raster working sets, and stale-backup recovery that would roll executable rows backward.

The 16-opcode ISA is unchanged. Spatial PNG v1 changes machine geometry and persistence/state handling, not opcode numbering.

Remaining validation: dedicated resident-vs-path performance benchmarks, sustained same-frame mutation soak, deliberate power-loss testing during spatial commits, and multi-node spatial workload execution on separate physical/VM hosts.



## Spatial resident-path benchmark — 2026-10-05

A dedicated deterministic benchmark now separates resident raster execution from PNG decode/checkpoint and compatibility persistence costs. GitHub Actions run 37347994293 on Ubuntu 24.04 / Python 3.11.16 measured:

- resident spatial VM: 27.0 µs mean, 37,033 ops/s;
- file-decoded spatial VM: 93.6 µs mean, 10,688 ops/s;
- resident execution speedup over per-call PNG decode: 3.47x;
- same-frame agent transaction including lock, decode, VM execution, binary memory update, PNG checkpoint, fsync, and backup handling: 1.766 ms mean, 566 ops/s;
- compatibility one-PNG-per-key agent-memory update for the equivalent four-key state payload: 12.218 ms mean, 81.8 ops/s;
- same-frame spatial transaction speedup over compatibility persistence: 6.92x;
- canonical JSON encode/decode alone: 7.96 µs mean.

The benchmark deliberately excludes LLM/provider inference and network latency. These results demonstrate local VM/state-path gains only. The 65.4x difference between resident VM execution and the full same-frame checkpoint path also confirms that PNG persistence, not opcode execution, is currently the dominant local cost once a checkpoint is required.

Machine-readable evidence is stored in `validation_results/spatial_resident_benchmark.json`; the reproducible harness is `zdx_spatial_benchmark.py`.

## Android policy and work-console pass 17.0

The Android node control surface now exposes the policy values actually enforced
by admission: compute enablement, idle-only behavior, charging requirement,
compute memory limit, and minimum free-memory reserve. Idle-only is based on
whether the device is interactive rather than Android Doze mode. Android
low-memory pressure is fail-closed.

Policy is enforced before polling, on every artifact-transfer continuation,
immediately before adapter execution, and from the adapter cancellation path
while work is running. A policy change can therefore release/preempt a task
instead of allowing an older admission snapshot to continue.

A persistent bounded work-event console records node registration/polling and
task lifecycle states: incoming, queued, receiving/download progress, running,
outgoing result delivery, completed, released, failed, and transport errors.
The live status panel shows current task/progress, last task and completed-work
detail, policy eligibility reason, idle/charging state, free RAM, configured
compute limit, and free-memory reserve.

A dedicated GitHub Actions Android debug-build gate now compiles the Kotlin APK
for Android-touching pull requests and uploads the debug APK as a workflow
artifact. Device-runtime validation is still required for final release
acceptance.

### Android low-resource support mode

Android mesh participation is now separate from compute admission. A node can
remain connected while compute is disabled, the phone is active, charging is
required but absent, memory pressure is high, or the configured reserve blocks
work.

The node advertises one of four participation modes: sync, light,
compute-idle-only, or compute. In sync/light mode it still performs
authenticated identity/capability refresh, heartbeat, availability reporting,
queue observation, and cluster-routing/master visibility. It does not poll for
or execute compute work until local policy becomes eligible again.

Heartbeat responses remain backward-compatible as `heartbeat` messages while
carrying optional queue counts and cluster-routing metadata for newer clients.
This preserves existing node compatibility while allowing the Android console
to display network workload and routing state.

### Authenticated-node rectification queue

Support-mode trust observation now has a durable advisory queue. The server
persists first/last successful authenticated activity and can queue active
identities for freshness review after a configurable trust-age interval
(default 24 hours). Verified replay-protection and authenticated rate-limit
failures can also queue review.

Enrolled peers, including Android support nodes, have a signed
`rectification_request` protocol operation for requesting review of another
active enrolled peer. Requests are reason-bounded, rate-limited, coalesced,
and may carry a SHA-256 evidence digest. A request does not alter karma,
suspend, quarantine, revoke, or otherwise punish its target.

Heartbeat payloads expose the aggregate pending rectification count and
configured re-attestation interval. Android advertises rectification-request
support and displays the pending trust-review count in its status/console.

The queue intentionally stops before enforcement. A subsequent challenge /
re-attestation workflow must resolve queued requests and bind any enforcement
decision to verified evidence and the existing karma/revocation policy.

### Lightweight authenticated re-attestation

The advisory rectification queue now has a low-cost completion path for
`authentication_age` reviews. Heartbeats can return a two-minute,
one-time Ed25519 challenge bound to the exact review request, node identity,
nonce, and integer millisecond timestamps. Android automatically signs that
challenge with its Keystore-backed enrolled key and returns the proof over the
already authenticated connection.

Successful proof updates the persisted re-attestation freshness baseline and
resolves only the matching stale-auth review. Suspicious behavior,
replay-pattern, and rate-limit-pattern reviews intentionally remain pending.
No compute lease, artifact download, VM execution, or hidden compute-policy
override is involved.

## Comprehensive release validation pass 18.0

A dedicated cross-subsystem release-validation workflow now checks the complete
Python suite on Python 3.11 and 3.12, repeated authentication/re-attestation/TLS
and network-fault scenarios, repeated distributed compute/resource/lease/spatial
admission scenarios, persistence/corruption/power-loss recovery, and Android
debug assembly plus lint.

The integrated release tests exercise a real authenticated socket round-trip for
stale-authentication re-attestation, invalid inner-proof rejection, one-time
challenge behavior, review-scope separation, resource-policy RAM budgeting,
distributed worker selection, lease persistence/restart, and coordinator RAM/CPU
reservation across concurrent running tasks.

This pass found and fixed a coordinator resource-accounting defect: repeated
worker polls could previously evaluate each new task against the same reported
available RAM/CPU without subtracting resources reserved by that worker's
existing leases. Claims now subtract running-task RAM and thread reservations
and are capped by registered static/safe worker limits, preventing lease-level
overcommit.

## Pyxel-native agent foundation pass 19.0

The first core-agent architecture pass is implemented on the feature branch:

- `SpatialAgentSession` owns resident frame/VM/checkpoint lifecycle.
- Agent ABI v1 binds semantic roles to named non-executable spatial regions.
- ABI metadata is layout-hash-bound and persisted with same-frame agent state.
- Native bounded binary mailboxes provide FIFO in-raster IPC with sequence and
  SHA-256 integrity checks.
- Same-generation durability uses checkpoint request IDs so an exact barrier
  cannot be satisfied by an older snapshot with the same VM generation/hash.
- `SpatialFrame.dirty_rectangles` is now a real tracked property for resident
  mutations rather than a planned/undefined optimization hook.

This pass deliberately does not yet add capability/tool execution authority,
memory namespace quotas/compaction, event replay journals, or incremental PNG
encoding. Those build on this ABI/session foundation in the next passes.

