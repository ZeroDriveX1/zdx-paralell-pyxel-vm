# ZDX Pyxel 1.0.0 release notes

This release freezes the Linux/core distributed-compute architecture for
external deployment validation while preserving karmic/gravitational election
and the 100-node cluster cap.

Included implementation:

- automatic signed gossip for durable queue, lease, result, failure, and
  contribution state with deterministic partition/rejoin recovery;
- encrypted immutable artifacts with chunked checkpoint/resume, scoped grants,
  final SHA-256 verification, and safe discard;
- disabled-by-default resource policy and Linux systemd/cgroup enforcement;
- capability-driven installer, `zdx-server`, `zdxctl`, update/uninstall,
  status/logs, checksums, and operator-controlled manifest signing tooling;
- Android Keystore mesh identity, dynamic capability negotiation, resumable
  artifact receive, foreground-service admission, fail-closed adapter registry,
  bounded integrity probe, cancellation/timeout boundary, and result
  attestation verification on the server.

Release gates:

- two-clean-Linux-machine install/restart/reboot/partition/rejoin/interrupted-
  transfer validation is documented but not claimed until run externally;
- Android APK build/sign/install/runtime validation requires a trusted Android
  SDK/device environment;
- ordinary Android Pyxel execution remains unsupported until a separately
  signed adapter satisfies `docs/ANDROID_VM_ADAPTER_CONTRACT.md`;
- release manifest and APK signing require operator-controlled private keys and
  are never performed with keys stored in this repository or CI logs.

See [VERIFICATION_REPORT.md](VERIFICATION_REPORT.md) for the strict distinction
between successful workspace checks and external checks that were not testable.
