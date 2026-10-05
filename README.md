# ZDX Pyxel

ZDX Pyxel is a deterministic pixel-native VM with a private, opt-in
distributed compute fabric. The runtime supports the spatial PNG v1 machine
model, where executable rows and non-executable addressable state regions can
share one standards-valid RGB PNG. Karmic/gravitational election remains the source
of cluster leadership; there is no fixed coordinator. Each cluster is capped
at 100 nodes and nodes may belong to multiple clusters.

## Start here

The complete operator workflow is in
[docs/USER_GUIDE.md](docs/USER_GUIDE.md). It covers release verification,
Linux/VPS installation, automatic capability profiling, TLS/enrollment,
worker policy, artifact submission, failover, Android build/install/use,
updates, uninstall, and troubleshooting.

The shorter deployment reference is
[docs/DISTRIBUTED_DEPLOYMENT.md](docs/DISTRIBUTED_DEPLOYMENT.md).

## Linux quick start

```bash
sudo env ZDX_CA_FILE=/etc/zdx-worker/ca.pem \
  ZDX_COORDINATOR_HOST=cluster-peer.internal \
  ZDX_NODE_ID=vps-01 ./packaging/install.sh
sudo zdxctl status
sudo zdxctl capabilities
sudo zdxctl policy enable
sudo zdxctl start
```

Installation discovers CPU/RAM capabilities, writes a conservative profile,
and keeps compute disabled until explicitly enabled. On connection, the
worker sends signed identity, capability report, and worker registration to
the connected cluster endpoint. Live load/free-RAM/process/guard checks can
only restrict the generated limits; they never grant unrestricted host use.

## Safety and security

The preserved mutual-session transport and operations pass is documented in [`docs/session-security/README.md`](docs/session-security/README.md).


Artifacts are immutable SHA-256-addressed blobs encrypted at rest with
AES-GCM. Artifact reads require authenticated task-scoped grants. Production
transport requires TLS plus Ed25519 peer admission. Workers do not execute
arbitrary remote Python or shell code and do not expose general filesystem
paths. Keep private keys out of source, manifests, logs, and service output.
Do not weaken SSH, firewalls, Android permissions, or existing VM isolation.

## Development and verification

```bash
python3 -m compileall -q .
python3 -m pytest -q
bash build.sh
```

See [release/VERIFICATION_REPORT.md](release/VERIFICATION_REPORT.md) for
passed checks and external environment blockers. Native and Android release
builds require their respective toolchains, signing keys, and (for Android) a
real device.

MIT License. See [LICENSE](LICENSE).

## Spatial PNG v1

The canonical spatial runtime uses standards-valid RGB PNG frames as executable and state containers. Executable rows use the existing 16-opcode ISA; rows outside the execution plane are addressable data and may host agent memory.

For same-frame agent workflows, the runtime keeps the decoded raster resident and advances a deterministic VM generation/hash on every execution. Ordinary durability is asynchronous and interval-based (default: every 10 executions); exact barrier checkpoints remain available for consequential actions and shutdown. Spatial task admission validates geometry, protocol version, worker capabilities, execution-row count, conservative raster working-set budget, and running-lease RAM/thread reservations.

See [SPATIAL_PNG.md](SPATIAL_PNG.md) for the machine contract and [ARCHITECTURE.md](ARCHITECTURE.md) for system integration.



## Current distributed-node state

Android and other constrained nodes can remain authenticated mesh participants even when normal compute is disabled by local policy. Support mode provides capability refresh, queue/routing visibility, authenticated heartbeat, advisory rectification requests, and lightweight Ed25519 re-attestation without downloading artifacts or executing Pyxel workloads.

The coordinator now subtracts RAM and thread reservations from already-running leases before assigning additional work, and also honors registered static/safe worker limits. Release validation covers full Python suites on 3.11/3.12, repeated auth/re-auth/network-fault passes, repeated distributed compute/RAM/lease passes, persistence/power-loss recovery, and Android debug assembly plus lint. Physical Android-device and multi-host long-duration validation remain separate operational gates.
