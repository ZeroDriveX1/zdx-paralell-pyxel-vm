# ZDX Development Roadmap

## Frozen Linux/core architecture

The Linux/core distributed-compute architecture is frozen for release
validation. It includes karmic/gravitational election with the 100-node cap,
authenticated transport, automatic durable state gossip, resumable scoped
artifacts, durable contributions, policy admission, and systemd/cgroup
enforcement. Core changes should be made only for defects found during the
clean-machine deployment gate.

## Active release work

1. Execute the two-clean-Linux-machine validation in
   `docs/CLEAN_LINUX_INTEGRATION_TEST.md`.
2. Produce the operator-signed checksum chain and release manifest using
   `packaging/sign_release.sh`.
3. Build/sign/install/runtime-test the Android APK on a real device.
4. Implement and separately sign a Pyxel Android VM adapter satisfying
   `docs/ANDROID_VM_ADAPTER_CONTRACT.md`.
5. Validate Android capability negotiation, bounded execution, cancellation,
   result attestation, reconnect, and device-priority preemption.

## Later enhancements

- additional signed Android and laptop VM adapters;
- hardware acceleration adapters after capability and isolation review;
- larger-scale operational dashboards and federation tooling.
