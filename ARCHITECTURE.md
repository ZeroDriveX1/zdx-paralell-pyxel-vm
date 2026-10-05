# ZDX Parallel Pyxel VM architecture

ZDX separates deterministic VM execution from authenticated transport,
capability discovery, resource admission, durable queue state, and artifact
mobility. The distributed unit is a bounded content-addressed frame task, not a
shared physical-memory process.

## Spatial PNG machine model

The current runtime supports the restored spatial PNG v1 contract. A standards-valid RGB PNG is both executable artifact and addressable state container: X/Y provide cell location/topology, R dispatches the existing 16-opcode ISA in executable rows, and G/B carry operands or data. Rows outside the declared execution plane are never scheduled and may be subdivided into named storage regions.

Agent memory can use a dedicated spatial PNG or bind to a named storage region in the same executable frame. The active spatial-memory backend uses deterministic binary typed records with checksums and atomic commits rather than JSON text. Same-frame agent execution uses one locked resident-raster transaction: decode once, execute, mutate only the declared data region, and checkpoint once. A failed transaction does not publish a new generation, and backup recovery cannot roll executable rows backward. Spatial task metadata is capability-gated before lease; version, geometry, execution-row count, and a conservative raster working-set budget are validated before execution.

## Runtime layers

- `zdx_parallel_vm.py`: deterministic local Pyxel VM execution and existing
  isolation boundaries.
- `zdx_compute_core.py` / `zdx_compute.py`: durable queue, worker leases,
  RAM/CPU admission, timeout/failure recovery, and contribution accounting.
- `zdx_resource_policy.py`: disabled-by-default idle/charging/guard/protected-
  process policy with immediate preemption support.
- `zdx_capabilities.py`: Linux profile discovery and conservative safe limits.
- `zdx_artifacts_core.py` / `zdx_artifacts.py`: encrypted immutable blobs,
  task/peer grants, chunk sessions, checkpoint/resume, and final hash checks.
- `zdx_network.py` / `zdx_node/`: bounded envelope framing and authenticated
  client transport.
- `zdx_server_core.py` → `zdx_server_gossip_core.py` →
  `zdx_server_chunked.py` → `zdx_server.py`: server composition preserving the
  existing compute handlers while adding gossip and chunk mobility.
- `zdx_cluster.py`: karmic master election and operation-log state materializer.
- `zdx_gossip.py`: persistent-sequence signed peer push/pull exchange.

## Election and replicated state

Every server joins the existing karmic/gravitational cluster and calculates the
leader from the existing reputation/mass rules. No server address is embedded
as a fixed coordinator. Peer endpoint lists are bootstrap routes only. A
cluster remains capped at 100 nodes and multi-cluster membership is preserved.

Queue, lease, result, failure, and contribution changes are operation records
with integrity hashes. Peers continuously exchange snapshots. Rejoin merges the
operation union and orders it deterministically, so master changes and
partitions do not require manual queue recovery.

## Resource safety

Local admission and Linux systemd/cgroup enforcement are intentionally
independent. Policy checks protect device/application workloads; CPU quota,
memory high/max, task count, low CPU/I/O weight, nice level, protected paths,
OOM stop behavior, and the production guard provide hard boundaries. A yielded
or expired lease returns to replicated queue state.

## Android boundary

The Android service uses the same authenticated envelope and task-scoped
artifact semantics. It detects CPU/RAM/charging/GPU/NPU capability facts and
persists safe settings. It can receive, resume, verify, and return a bounded
artifact probe. It fails closed for ordinary Pyxel workloads because the APK
does not embed the Python/Pyxel VM; Android is not advertised as a full Pyxel
worker until that signed adapter and device validation exist.

## Asynchronous spatial checkpoints

Same-frame spatial agent execution keeps the decoded frame and agent state resident. The VM advances a deterministic execution generation and checkpoint hash on every completed execution. Ordinary durability is interval-based (default: every 10 executions), not per-execution.

Checkpoint requests are handled by a single coalescing worker. Non-barrier requests are latest-wins so persistence cannot accumulate an unbounded backlog when compute runs faster than PNG encoding. A barrier checkpoint forces the exact requested generation durable before returning.

Each durable checkpoint records two identities:

- `vm_checkpoint_hash`: SHA-256 over canonical VM generation/clock/register/shared state;
- `artifact_sha256`: SHA-256 over the exact committed PNG bytes.

The checkpoint worker independently recomputes the VM hash from the frozen snapshot before commit. Checkpoint commits use artifact compare-and-swap protection so an external frame mutation cannot be overwritten silently. On restart, the runtime verifies and restores the last durable generation before continuing.



## Support-mode trust and lightweight re-attestation

Node connectivity and compute admission are separate concerns. A node whose local resource policy blocks Pyxel execution may remain connected in sync/light support mode. It can refresh capabilities, report availability, observe queue/routing state, submit bounded rectification requests, and answer short-lived re-attestation challenges. None of those operations grant a compute lease or override local resource policy.

Rectification is advisory. Long-lived authentication, verified replay patterns, or authenticated rate-limit anomalies may queue a review, but review state does not directly alter karma, quarantine, suspension, or revocation. Authentication-age reviews can be resolved by a one-time Ed25519 possession proof bound to the exact request ID, node ID, random nonce, and expiry.

## Lease-level resource accounting

Distributed RAM/CPU admission is reservation-based rather than per-poll. A worker's effective claim budget is bounded by its reported availability, registered static capability, configured safe limit, and resources already reserved by that worker's running leases. This prevents repeated polling from overcommitting the same RAM or execution threads.

## Pyxel-native Agent Module v1 foundation

The Pyxel-native Agent Module remains intentionally separate from ZDX AgentCore.

`SpatialAgentSession` now owns a same-frame resident raster, VM state activation/recovery, Agent ABI metadata, native mailboxes, dirty resident state, and asynchronous checkpoint lifecycle. `ZDXAgentRuntime` delegates same-frame execution to this session rather than directly managing frame/checkpoint dictionaries.

Agent ABI v1 binds semantic roles to distinct named non-executable regions and pins those bindings to a SHA-256 of the exact `SpatialLayout`. A persisted ABI cannot be silently rebound to another region layout after restart. Current roles are persistent memory, working memory, inbound/outbound mailbox, capabilities, and provenance.

Native spatial mailboxes are bounded binary FIFO rings stored directly in ABI-bound PNG regions. Messages carry monotonic sequence numbers, sender/recipient/topic metadata, bounded payloads, and a domain-separated SHA-256 integrity digest. Corrupt slots fail closed.

Checkpoint barriers now use an internal monotonic snapshot ticket in addition to VM generation. This matters because mailbox/state mutations can occur without executing another VM instruction; an exact barrier waits for the specific frozen snapshot rather than merely observing that the same VM generation was previously committed.

The next layers build on this foundation: namespaced/quota-aware memory, capability/tool authorization, replay/event journal, and dirty-region tracking.
