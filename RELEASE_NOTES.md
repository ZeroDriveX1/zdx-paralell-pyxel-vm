# ZeroDriveX Release Notes

## Pass 21.0 — Pyxel-native Tool Execution Control

The Pyxel-native Agent Module now has an explicit capability boundary for
external/tool actions. Tools must be explicitly registered and explicitly
granted by a versioned policy; there is no generic shell, path, or ambient
execution fallback.

A canonical action hash binds the exact tool request, idempotency key, VM
generation/checkpoint identity, and policy hash. Barrier-required actions write
and durably checkpoint a pending intent before invoking the handler. Successful
results are durably recorded afterward. A retry of the same action identity can
reuse the recorded result rather than repeat the side effect; incomplete
outcomes fail closed as ambiguous.

An optional external authorization interface is designed for Axiomatic Runtime
or equivalent policy engines. External decisions must echo the exact action and
policy hashes. Deny, approval-required, stale/mismatched decisions, malformed
responses, and authorizer failures do not execute the handler.

Safety intent/result namespaces are non-evicting by default so idempotency
records cannot disappear silently. Capacity exhaustion therefore stops new
actions instead of weakening duplicate-execution protection.

The action identity currently binds VM generation/checkpoint state, not every
resident memory/mailbox byte. Broader resident-state and event lineage is the
next provenance/replay layer.


## Pass 20.0 — Namespaced and Quota-aware Pyxel Agent Memory

The Pyxel-native Agent Module now has deterministic logical memory namespaces
inside the same persistent region owned by `SpatialAgentSession`. Working,
episodic, facts, tool-results, and system memory can be independently bounded
by encoded bytes and entry count.

Quota decisions use the actual deterministic binary encoding. A mutation must
fit both its namespace policy and the complete physical persistent-region
capacity before resident state is changed. Reject-mode operations are atomic;
FIFO namespaces evict only their oldest deterministic writes.

The manager does not create another PNG writer. Changes remain resident and are
made durable through the session's existing checkpoint/barrier lifecycle,
preserving artifact CAS lineage. Persisted policy metadata and sequence state
fail closed when corrupted or silently changed.

The optional ABI working_memory region remains reserved for a later
resident-region codec; this pass establishes the namespace contract without
creating competing file-level persistence.


## Pass 19.0 — Agent ABI v1, SpatialAgentSession, and Native Mailboxes

The Pyxel-native Agent Module now has a versioned same-frame foundation separate from ZDX AgentCore. `SpatialAgentSession` owns resident spatial execution, VM state activation/recovery, ABI metadata, native mailbox access, dirty-state tracking, and checkpoint lifecycle.

Agent ABI v1 binds semantic roles to distinct named storage regions and pins the binding to the exact canonical `SpatialLayout` hash. Native inbound/outbound mailboxes use bounded binary FIFO rings with monotonic sequence numbers and integrity digests.

Checkpoint barriers now carry monotonic snapshot tickets in addition to VM generation so state-only mutations at the same VM generation cannot cause an exact barrier to return before the requested frozen snapshot is durable. Clean close also forces dirty state durable before shutdown.

This pass also defines `SpatialFrame.dirty_rectangles` for resident mutation tracking. Subsequent work will use those rectangles for memory namespaces/quotas, capability/tool authorization, replay journaling, and dirty-region-aware checkpoint/tiled-copy optimization.


## Pass 18.0 — Support Mode, Lightweight Re-auth, and Comprehensive Validation

Android policy enforcement now controls compute admission without disconnecting the node from the mesh. Constrained nodes can remain useful in sync/light support mode for authenticated heartbeat, capability refresh, queue/routing visibility, advisory trust-review requests, and lightweight Ed25519 re-attestation.

Rectification is advisory and rate-limited. Authentication-age reviews can be resolved by a one-time short-lived possession proof over a challenge bound to the exact request/node/nonce/expiry. Successful re-attestation resets trust freshness only; suspicious/replay/rate-limit reviews remain pending for separate evidence review.

Distributed compute resource accounting now subtracts RAM and thread reservations from a worker's already-running leases before another claim is admitted. Static and configured safe worker limits also cap the effective budget, preventing repeated-poll overcommit.

A permanent comprehensive release-validation workflow now runs the complete Python suite on Python 3.11/3.12, repeated auth/re-auth/network-fault validation, repeated distributed compute/RAM/lease validation, persistence/power-loss recovery, and Android debug assembly plus lint.

Repository CI validation for this pass was green. This is not a claim of physical-device, multi-host long-duration, or fleet-scale production certification.


## Pass 16.0 — Spatial PNG v1 Integration and Agent-State Hardening

The validated spatial subsystem from the earlier PyxelVM line is integrated into the current hardened parallel runtime without replacing the newer authentication, persistence, distributed-compute, resource-policy, or Android work.

The canonical machine contract is a standards-valid RGB PNG whose decoded raster is the machine representation. X/Y provide deterministic cell addressing; R is the opcode dispatch byte in executable rows; G/B are operands or data; declared storage rows remain non-executable. The existing 16-opcode ISA is unchanged.

Agent memory can bind to a named non-executable region of the same spatial PNG. Same-frame agent execution now has a resident transaction path: one locked PNG decode, resident VM execution, deterministic binary memory update, and one atomic checkpoint. This avoids repeated PNG decode/recompression inside the agent state transition while preserving PNG as the durable executable/state container.

Hardening in this pass includes strict RGB-PNG acceptance, canonical layout/version validation, derived-capacity checks, bounded total cell count, spatial compute admission tied to execution-row count and task memory budget, and fail-closed recovery that will not restore a stale backup when its executable plane differs.

Spatial artifacts remain content-addressed by the SHA-256 of the exact PNG bytes. A memory/state checkpoint therefore creates a new frame identity; manifests describe a specific immutable generation and must be regenerated after an intentional state mutation.

Performance claims remain benchmark-dependent. PNG compression is a checkpoint/storage mechanism, not the source of execution acceleration. The intended hot path is the resident decoded raster; persistent PNG encoding occurs at explicit transaction/checkpoint boundaries.


## Pass 13.0 — Production Validation and Operational Hardening

This pass adds validation tooling rather than a new runtime subsystem: lightweight metrics, deterministic failure injection, bounded/duration soak execution, persistence stress, fuzz campaigns, and machine-readable benchmarks.

Local evidence on 2026-07-30:

- 156 automated tests passed;
- 100 integrated soak cycles passed in 24.87 seconds;
- 1,033 soak commits averaged 12.07 ms, maximum 42.60 ms;
- zero thread, descriptor, stale-transaction, post-warmup lock, or backup growth;
- 1,000-commit stress passed in 20.05 seconds at 49.86 commits/second;
- deterministic recovery and bounded fuzz suites passed;
- all nine benchmark workloads executed successfully.

The 24-hour mode was not run. No physical multi-machine, power-loss, TLS, NFS/distributed-filesystem, Android, or fleet-scale validation was performed. This release is an authenticated and transactionally persistent foundation with bounded local operational evidence, not an unconditional production certification.

## Pass 14.0 — Distributed Deployment and RC2 (candidate, acceptance pending)

Mutual TLS is now the default network transport, with peer certificates, hostname checks, CRL support, and reloadable server contexts. A reconnecting worker entrypoint, scheduler lifecycle integration, Docker/systemd/Windows examples, and deployment/operations/backup guidance were added.

Local evidence: 14 focused tests passed on one ARM64 Android/Termux host using one coordinator and three isolated worker processes. This validated process isolation and real loopback sockets, not separate machines. The 24-hour soak, physical/VM multi-node topology, capacity-to-failure measurement, privileged fault injection, physical power-loss, Docker, systemd, and Windows service validation were not available. RC2 acceptance is therefore pending and no production-ready claim is made.

Pass 14 local microbenchmark rates (100-iteration scale configuration): VM 640.28 ops/s, persistence 39.27 commits/s, scheduler selection 13,008.43 ops/s, registry lookup 885,571.27 ops/s, Ed25519 signing 22,205.66 ops/s, verification 6,858.14 ops/s, PNG serialization 248.01 ops/s, migration 22.23 ops/s, and authenticated coordinator dispatch 2,455.67 ops/s. Peak RSS was 104,684 KiB with five open descriptors at capture. These are microbenchmarks, not fleet capacity limits; maximum connected nodes, coordinator CPU saturation, and recovery time under real partitions remain unmeasured.
