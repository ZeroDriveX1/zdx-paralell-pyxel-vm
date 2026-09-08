# ZDX Pyxel 1.0.0 verification report

This report explicitly separates successful tests from paths that could not be
tested in this workspace. Missing tools or external machines are environment
limitations, not product failures; those paths are not marked verified.

The detailed acceptance matrix is
[docs/ACCEPTANCE_MATRIX.md](../docs/ACCEPTANCE_MATRIX.md).

## Tested successfully in this workspace

- Python compilation, imports, whitespace, installer/launcher/controller
  syntax, and release checksum validation.
- Authenticated Ed25519 identity admission, signatures, replay protection, and
  TLS helper validation.
- Two-node authenticated server-thread integration with signed registration,
  automatic gossip, and queue convergence.
- Authenticated chunked artifact upload/download, offset validation, scoped
  grants, final SHA-256 verification, expiration, and incomplete-transfer
  rejection.
- Resumable artifact checkpoint behavior, lease release/expiry recovery,
  deterministic partition/rejoin materialization, and contribution recovery.
- Durable queue reload, RAM/CPU admission, production-guard preemption, and
  focused karmic election/state recovery.
- Python server verification of a valid enrolled-node Android result
  attestation and rejection of an altered/invalid attestation.
- Release inventory/checksum/signing-script syntax and coverage logic.
- Full Python regression suite executed with the repository validation environment: `./.venv-release/bin/python -m pytest -q` → **120 passed, 0 failed in 0.58s**. The previously reported authenticated-network failure was fixed by making `ZDXNode.connect()` consume and validate `identity_ack` before returning the socket.

## Not testable in this workspace

These paths were not run and must not be interpreted as passed:

- Two clean Linux machines with real systemd installation, host reboot,
  firewall partition/rejoin, process restart, and interrupted artifact
  transfer. Run [CLEAN_LINUX_INTEGRATION_TEST.md](../docs/CLEAN_LINUX_INTEGRATION_TEST.md).
- Two-host TLS/mTLS validation with operator certificates, CA files, and real
  firewall rules.
- Android Gradle compilation, APK signing, adb installation, foreground
  service runtime, phone-to-VPS task cycle, adapter timeout/cancellation, and
  device-priority yield behavior.
- Production signing with operator-controlled Android and Ed25519 keys.
  No private signing key is present here; use [RELEASE_SIGNING.md](../docs/RELEASE_SIGNING.md).

## Acceptance result summary

| Acceptance | Result |
| --- | --- |
| Clean A install/enroll/advertise → clean B install/enroll → election → adapter-ID workload → authorized artifact → claim → bounded execution → attested result → contribution/result acceptance | NOT TESTABLE IN THIS WORKSPACE; execute on two clean hosts |
| Unknown adapter | SOURCE-LEVEL ONLY; registry rejects unregistered IDs |
| Wrong adapter protocol | SOURCE-LEVEL ONLY; registry rejects mismatches |
| Expired grant | TESTED SUCCESSFULLY |
| Modified artifact | TESTED SUCCESSFULLY; final SHA-256 rejects it |
| Timeout/cancellation | SOURCE-LEVEL ONLY for Android; device runtime required |
| Worker disappearance/lease recovery | TESTED SUCCESSFULLY in focused Python state checks |
| Master disappearance/election/state convergence | SOURCE-LEVEL/FOCUSED ONLY; clean-host failover required |
| Altered result attestation | TESTED SUCCESSFULLY by server regression check |
| Busy-host yield | TESTED SUCCESSFULLY for Linux; Android device path not testable |
| APK and signed manifest/checksum chain | NOT TESTABLE IN THIS WORKSPACE; operator keys/toolchains required |

## Release status

Linux/core architecture is frozen pending the external clean-machine gate.
Further core changes should be limited to defects found there.

Android ordinary Pyxel tasks remain fail-closed until a separately signed VM
adapter satisfies [ANDROID_VM_ADAPTER_CONTRACT.md](../docs/ANDROID_VM_ADAPTER_CONTRACT.md)
and passes build, installation, runtime, attestation, and real phone-to-VPS
validation.
