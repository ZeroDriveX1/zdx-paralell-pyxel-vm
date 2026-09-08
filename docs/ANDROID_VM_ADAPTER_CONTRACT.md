# Android VM adapter contract

This contract defines the boundary for a future signed Android Pyxel VM
adapter. The current APK implements the registry, negotiation, bounded
integrity probe, cancellation path, and result attestation, but intentionally
does not register a Pyxel VM adapter. Ordinary Pyxel tasks therefore remain
fail-closed.

## Contract version and manifest

The protocol version is `1`. Every adapter must ship a signed manifest with:

```json
{
  "adapter_id": "zdx.pyxel.android",
  "protocol_version": 1,
  "artifact_types": ["pyxel-frame-v1"],
  "max_artifact_bytes": 8388608,
  "max_memory_mb": 512,
  "max_runtime_seconds": 300,
  "capabilities": ["pyxel-vm", "frame-hash", "checkpoint-yield"],
  "manifest_sha256": "...",
  "signature": "..."
}
```

The signature is verified against an operator-controlled adapter release key.
The manifest digest is included in every result claim. An adapter with an
unknown ID, unsupported protocol, invalid signature, or incompatible limits is
not admitted.

## Capability negotiation

Android capability registration includes:

- `android_vm_adapter_protocol`;
- `android_vm_adapters`, containing only locally installed/verified adapters;
- CPU count, total/available RAM, charging/battery state, GPU/Vulkan/OpenGL,
  NPU/NNAPI facts, and safe memory/concurrency limits.

A task must explicitly request `metadata.android_vm_adapter_id` and may include
`metadata.required_android_capabilities`. The scheduler must select only an
adapter whose manifest satisfies the request and the node’s current profile.
Missing metadata never implies a default adapter. The current built-in probe
may be requested explicitly with `android_bounded_probe=true`.

## Resource and isolation boundary

An adapter receives an immutable, verified artifact file inside the app-private
`filesDir/zdx-downloads` directory. It receives no host path, URI, socket,
secret, private key, or general filesystem handle. It may write only bounded
result data returned through the adapter API.

The adapter must enforce all of these limits:

- artifact size <= both the task/node limit and manifest limit;
- memory reservation <= both the task policy and manifest limit;
- runtime <= the task timeout and manifest maximum;
- output/result <= the protocol result limit;
- requested capabilities must be present in the node profile;
- no network access from the adapter execution boundary;
- no shell, dynamic code loading, or arbitrary remote code execution;
- no execution after policy revocation, service stop, charging loss, or device
  workload priority change.

## Cancellation and timeout

The adapter receives an `AndroidVmCancellation` callback. It must check the
callback between bounded work units and terminate promptly when it returns
cancelled. Cancellation occurs when:

- idle/charging/free-memory admission is revoked;
- the foreground service is stopped or destroyed;
- the host Android process is interrupted;
- the task timeout expires.

Policy cancellation yields/releases the lease so the task can resume. A hard
execution timeout produces a failed result and contribution record. A transport
failure leaves the lease recoverable for coordinator expiry/retry.

## Artifact I/O and verification

The transport downloads into a durable `.part` checkpoint, resumes by offset
after disconnect, obtains a fresh task-scoped grant, and verifies final
SHA-256. The adapter must independently verify that the received file is inside
the private directory and that its digest equals the task artifact digest before
reading it. Corrupt, incomplete, oversized, or expired-grant transfers are
discarded and never passed to an adapter.

## Result attestation

The returned result must include:

- task ID and input artifact SHA-256;
- adapter ID, protocol version, and manifest SHA-256;
- verification result and completed work;
- bounded execution timing and output/result digest.

The Android node signs the result claim with its Keystore Ed25519 identity. The
claim contains the public key, node ID, canonical result digest, and signature;
the enclosing ZDX message is independently signed by the same node identity.
The server must verify both signatures, the enrolled node key, the adapter
manifest digest, and the task/lease binding before accepting contribution or
result state.

## Current implementation status

Implemented now: versioned registry, capability advertisement, built-in bounded
integrity probe, private artifact checks, timeout/cancellation boundary, and
Keystore result attestation.

Not registered: a Pyxel VM adapter. Until a separately reviewed adapter
implements this contract and passes Android build/device tests, Linux remains
the Pyxel execution platform and Android ordinary tasks are rejected.
