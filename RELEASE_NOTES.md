# ZeroDriveX Release Notes

## Pass 20.0 — Namespaced Memory, Provenance, Capabilities, and Dirty Tracking

The Pyxel-native Agent Module now layers strict quota-aware logical memory over its persistent ABI region, with deterministic binary byte accounting, reject/FIFO policies, canonical compaction, and fail-closed persisted-schema validation.

A bounded hash-chained provenance journal can occupy the ABI provenance region and automatically records VM executions and mailbox transfers. The journal is intentionally finite; it is retained-window provenance, not yet a full replay WAL.

The default-deny capability gateway installs an integrity-checked binary grant table, supports exact action grants and approval-required states, and binds permitted action envelopes to canonical arguments plus an exact durable VM checkpoint and PNG artifact identity. It authorizes/prepares actions but does not execute tools. Explicit Axiomatic approval-artifact verification remains the next security integration.

Resident spatial frames now track conservative dirty rectangles and sessions track semantic dirty roles. These are optimization/audit metadata only; current durable PNG checkpoints still perform complete standards-valid encoding.


## Pass 19.0 — Agent ABI v1, SpatialAgentSession, and Native Mailboxes

The Pyxel-native Agent Module now has a versioned same-frame foundation separate from ZDX AgentCore. `SpatialAgentSession` owns resident spatial execution, VM state activation/recovery, ABI metadata, native mailbox access, dirty-state tracking, and checkpoint lifecycle.

Agent ABI v1 binds semantic roles to distinct named storage regions and pins the binding to the exact canonical `SpatialLayout` hash. Native inbound/outbound mailboxes use bounded binary FIFO rings with monotonic sequence numbers and integrity digests.

Checkpoint barriers now carry monotonic snapshot tickets in addition to VM generation so state-only mutations at the same VM generation cannot cause an exact barrier to return before the requested frozen snapshot is durable. Clean close also forces dirty state durable before shutdown.

This pass is the foundation for subsequent memory namespaces/quotas, capability/tool authorization, replay journaling, and dirty-region tracking.


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
