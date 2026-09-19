# ZeroDriveX Release Notes

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
