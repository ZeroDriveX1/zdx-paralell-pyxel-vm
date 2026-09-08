# ZDX Development TODO

## Completed implementation

- [x] Preserve karmic/gravitational master election and 100-node cluster cap.
- [x] Replicate queue, lease, result, failure, and contribution operations with
      deterministic partition/rejoin materialization.
- [x] Add encrypted immutable content-addressed artifacts, scoped grants,
      chunked checkpoint/resume, final SHA-256 verification, and safe discard.
- [x] Keep workers disabled by default with idle/charging/RAM/CPU,
      protected-process, guard-file, and immediate-yield controls.
- [x] Add Linux systemd/cgroup CPU, memory, task-count, nice, and I/O limits.
- [x] Add capability-driven Linux installer, server launcher, `zdxctl`, logs,
      update/uninstall paths, and release-manifest tooling.
- [x] Add Android mesh registration, dynamic capability advertisement,
      foreground-service admission, resumable artifact receive, fail-closed
      adapter registry, bounded probe, cancellation, and result attestation.
- [x] Add clean-machine validation runbook and operator-controlled signing
      script without repository signing keys.

## External release gates

- [x] Run the full Python suite in the provisioned validation environment
      (`120 passed, 0 failed`); native package and Android toolchain gates remain
      external release checks.
- [ ] Install and validate the signed release on two clean Linux machines,
      including restart, host reboot, partition/rejoin, and interrupted
      artifact-transfer recovery.
- [ ] Generate and independently verify the operator-signed checksum chain and
      Ed25519 release manifest.
- [ ] Build/sign/install/runtime-test the Android APK on a real device.
- [ ] Implement and validate a separately signed Android Pyxel VM adapter under
      `docs/ANDROID_VM_ADAPTER_CONTRACT.md`.

Linux/core feature development is frozen unless the clean-machine gate finds a
defect. Unsupported Android tasks must remain fail-closed.
