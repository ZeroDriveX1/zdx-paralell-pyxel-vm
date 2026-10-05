# ZDX Distributed Testing Plan

## Workspace checks

- Python compile/import checks and `git diff --check`.
- Focused authenticated identity, replay, TLS, gossip, lease, contribution,
  artifact, and resource-policy checks.
- Shell/Python syntax checks for installer, launcher, controller, and signing
  tooling.

## Spatial PNG v1 gate

- Reject non-PNG and non-RGB spatial frame inputs rather than converting them.
- Reject boolean/non-integer geometry, mismatched derived capacities, overlapping regions, and custom executable regions.
- Verify threads == execution_rows and reject spatial tasks whose estimated raster working set exceeds the declared task memory lease.
- Require spatial_version == 1 and canonicalize the same layout across manifests, protocol metadata, sync, and compute tasks.
- Verify storage rows containing opcode-looking RGB values are never scheduled.
- Verify a same-frame agent transaction decodes/executes/updates one resident frame and commits once.
- Verify an exception during a resident transaction leaves the previous PNG generation unchanged.
- Verify corrupt same-frame memory never restores a backup with a different executable plane.
- Verify executable rows remain byte-equivalent across memory-only state updates.

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


## Comprehensive release-validation gate

The repository now includes `.github/workflows/release-validation.yml`. The gate runs:

- the complete Python suite on Python 3.11 and 3.12;
- repeated auth, re-auth, TLS, replay and network-fault tests;
- repeated distributed compute, lease, RAM/thread and spatial-admission tests;
- persistence, corruption recovery and hard-kill/power-loss tests;
- Android debug assembly plus lint.

Integrated release tests also exercise a real authenticated socket flow from stale-auth review through heartbeat challenge, signed Ed25519 re-attestation, and scoped review resolution.

The gate intentionally does not substitute for physical Android-device testing, multi-host long-duration concurrency, or fleet-scale capacity testing.


## Agent ABI/session/mailbox gate

- Validate Agent ABI v1 layout hash and reject unknown versions or silent region rebinding.
- Reject duplicate role-to-region bindings and any binding that overlaps the execution plane.
- Verify native mailbox FIFO order, bounded capacity, sequence wrap through ring slots, and SHA-256 tamper detection.
- Verify mailbox and VM state survive a barrier checkpoint and restart in the same spatial PNG.
- Verify clean session close persists dirty generations before the normal checkpoint interval.
- Verify `SpatialFrame.dirty_rectangles` is always defined, bounded, clone-safe, clearable, and coalesces adjacent/overlapping writes.
- Verify an exact barrier at the same VM generation waits for the newer mailbox/frame snapshot rather than returning on an older generation-only checkpoint.

## Namespaced memory gate

- Verify namespace isolation and persistence across barrier checkpoint/restart.
- Verify reject-mode entry/byte quota failures are atomic.
- Verify FIFO eviction is deterministic by global write sequence.
- Verify a value that fits its declared namespace quota but cannot fit the
  actual persistent region is rejected before mutation.
- Verify memory changes remain resident until checkpoint/clean close.
- Verify persisted namespace policy changes fail closed unless an explicit
  migration mechanism is added.
- Verify malformed schema versions, duplicate/reused sequences, and regressed
  next-sequence metadata fail closed.
- Verify usage reporting is derived from deterministic binary encoded size.

## Agent tool-gateway gate

- Verify pending intent is durable before a barrier-required handler runs.
- Verify successful result and executed intent survive durability barriers.
- Verify same idempotency key returns the durable result without another tool
  execution, while a different idempotency key permits an intentional repeat.
- Verify ungranted tools, operations, resources, and unregistered handlers fail
  before handler execution.
- Verify external authorization must echo the exact action/policy hashes.
- Verify deny, approval-required, malformed, mismatched, and failed external
  authorizers all fail closed.
- Verify handler exceptions leave durable ambiguous intent and same-action retry
  does not execute again.
- Verify persisted result/intent proposal mismatch fails closed.
- Verify gateway safety namespaces reject eviction policies.
- Verify policy mismatch after restart fails closed.
- Verify canonical action hashes are stable across mapping key order and reject
  floating-point arguments.

