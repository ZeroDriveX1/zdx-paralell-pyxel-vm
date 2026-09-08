# ZDX Distributed Testing Plan

## Workspace checks

- Python compile/import checks and `git diff --check`.
- Focused authenticated identity, replay, TLS, gossip, lease, contribution,
  artifact, and resource-policy checks.
- Shell/Python syntax checks for installer, launcher, controller, and signing
  tooling.

## Clean Linux release gate

Run `docs/CLEAN_LINUX_INTEGRATION_TEST.md` on two newly provisioned machines.
Do not mark it passed from simulated threads or one host. Evidence must cover
identical signed release installation, both worker registrations, service and
host restarts, state/lease/result/contribution recovery, firewall-only
partition/rejoin, interrupted multi-chunk transfer, fresh grant, expired grant
rejection, and final digest verification.

## Android adapter gate

On a trusted Android build host and physical/test device:

- compile and sign the APK;
- verify the APK signature and release checksum/manifest chain;
- install/update/uninstall with the same signing identity;
- verify Keystore identity persistence and capability negotiation;
- verify idle/charging/free-memory admission and immediate yield;
- verify chunked artifact receive/resume/corruption discard;
- verify unsupported tasks fail closed;
- verify the bounded probe timeout/cancellation path;
- verify result attestation and server-side signature/lease acceptance;
- verify the future Pyxel adapter only after its signed manifest and contract
  checks pass.

## Security validation

- signatures cover envelope metadata and payload;
- untrusted/revoked peers and expired grants are rejected;
- private keys never appear in logs or release artifacts;
- adapters receive no general filesystem/network/shell access;
- Linux cgroups and production guard preserve local workload priority;
- release signing keys remain operator-controlled.

The full Python suite was executed successfully in the repository validation
environment: `./.venv-release/bin/python -m pytest -q` → **120 passed, 0 failed**.
The remaining Android/device and clean-host gates require external dependencies,
hardware, or machines; unavailable tooling is reported as “not testable in this
workspace,” not as a passed or failed product test.
