# ZDX Pyxel

ZDX Pyxel is a deterministic pixel-native VM with a private, opt-in
distributed compute fabric. Karmic/gravitational election remains the source
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
