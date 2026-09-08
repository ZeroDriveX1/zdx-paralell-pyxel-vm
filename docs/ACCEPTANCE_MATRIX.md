# Release acceptance matrix

Status meanings:

- **TESTED SUCCESSFULLY**: executed in this workspace with reproducible
  evidence;
- **NOT TESTABLE IN THIS WORKSPACE**: requires clean hosts, Android hardware,
  external certificates, or operator signing keys and is not claimed;
- **SOURCE-LEVEL ONLY**: logic is present and statically reviewed, but runtime
  execution of that platform path was unavailable.

| Acceptance | Current status | Evidence or required gate |
| --- | --- | --- |
| Full Python regression suite | TESTED SUCCESSFULLY | `./.venv-release/bin/python -m pytest -q`: 120 passed, 0 failed in 0.58s |
| Clean machine A install/enroll/capability advertisement | NOT TESTABLE IN THIS WORKSPACE | Two-clean-host runbook required |
| Clean machine B install/enroll | NOT TESTABLE IN THIS WORKSPACE | Two-clean-host runbook required |
| Karmic election and workload submitted by adapter ID | SOURCE-LEVEL ONLY | Existing focused election checks; clean-host adapter workload required |
| Encrypted artifact authorized and task claimed | TESTED SUCCESSFULLY | Authenticated network artifact and worker registration checks |
| Bounded execution and signed/attested result | SOURCE-LEVEL ONLY | Server attestation regression passed; Android runtime unavailable |
| Contribution recorded and result accepted | TESTED SUCCESSFULLY | Replicated contribution recovery and server result checks |
| Unknown adapter rejected | SOURCE-LEVEL ONLY | Registry rejects unregistered IDs; Android runtime unavailable |
| Wrong protocol rejected or safely negotiated | SOURCE-LEVEL ONLY | Registry rejects protocol mismatch; Android runtime unavailable |
| Expired artifact grant rejected | TESTED SUCCESSFULLY | Python artifact grant/transfer checks |
| Modified artifact fails SHA-256 | TESTED SUCCESSFULLY | Python chunk/final digest checks |
| Task timeout cancelled/released | SOURCE-LEVEL ONLY | Android timeout/cancellation code present; device runtime unavailable |
| Worker disappearance expires/recover task lease | TESTED SUCCESSFULLY | Lease expiry/requeue and gossip recovery checks |
| Master disappearance triggers karmic replacement/state convergence | SOURCE-LEVEL ONLY | Focused election/state checks; clean-host failover gate required |
| Altered result attestation rejected | TESTED SUCCESSFULLY | Python server attestation verification regression |
| Busy host/device yields immediately | TESTED SUCCESSFULLY for Linux; SOURCE-LEVEL ONLY for Android | Linux guard/preemption passed; Android device gate required |
| Production APK and manifest/checksum signing | NOT TESTABLE IN THIS WORKSPACE | Requires operator-controlled keys and Android toolchain |
