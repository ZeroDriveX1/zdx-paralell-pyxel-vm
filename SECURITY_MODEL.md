# ZDX Security Model

## Current Security Boundaries

ZDX separates communication from execution.

Current protections:

- deterministic local execution
- protocol compatibility checks
- frame hash verification
- persistent node tracking
- strict length-prefixed message validation
- opt-in Ed25519 signatures over the complete message envelope
- operator-controlled peer enrollment
- signature-first replay and rate-limit admission
- per-peer sequence replay protection
- verified frame SHA-256 before execution
- bounded task memory reservations and persistent leases
- disabled-by-default worker policy with idle and production-guard checks
- durable advisory rectification/re-attestation queue for authenticated peers
- replay/rate-limit patterns can request review without directly changing karma

Authenticated transport is enabled with `ZDXServer(require_auth=True)` and a
trusted public-key mapping. The default unsigned server mode exists for local
development compatibility and is not a security boundary.

## Remaining Security Work

The current tree already includes persistent trust/revocation state, TLS/mTLS-capable transport, bounded artifact/result paths, replay/rate-limit admission, and resource-aware leases. Remaining independent hardening includes:

- stronger capability attestation and versioned capability claims;
- explicit tool/action capability authorization for the Pyxel-native agent module;
- fleet-scale workload quotas and abuse controls;
- independent protocol fuzzing/penetration testing beyond the repository fuzz/stress suites;
- physical-device and multi-host adversarial validation.

The worker does not execute arbitrary Python, shell commands, or user-supplied
code. Its current workload is a local, hash-verified PNG frame. CPU and memory
limits must be enforced by both the JSON policy and the host service manager;
the policy alone cannot provide a hard CPU quota on every operating system.

Security decisions remain modular so deployments can choose appropriate trust levels.

## Rectification / re-attestation boundary

Rectification means revalidation of an already enrolled identity. It is not a
revocation, suspension, or negative-karma event. Authenticated peers may submit
signed review requests for active enrolled nodes; requests are bounded,
rate-limited, and coalesced. The local authentication monitor may also queue a
request after a verified replay/rate-limit pattern or when an actively used
identity exceeds the configured trust-age interval.

The default trust-age interval is 24 hours and can be changed with
`ZDX_REATTEST_AFTER_SECONDS` or the server constructor. This is a freshness
trigger, not evidence of misconduct. Actual challenge/attestation resolution,
karma changes, quarantine, suspension, and revocation remain separate policy
steps and require verified evidence.

## Lightweight re-attestation

Stale-authentication rectification requests can now be resolved with a
short-lived Ed25519 possession proof. The server issues a challenge bound to
`zdx-reattest-v1`, the rectification request ID, target node ID, a random
nonce, and integer millisecond issue/expiry times. The enrolled node signs that
canonical payload with its existing private key.

Challenges expire after two minutes by default, are one-time use, and are bound
to one node and one pending authentication-age review. Successful
re-attestation resets that node's trust-age freshness baseline and resolves
only the stale-authentication review. Replay/rate-limit/suspicious-behavior
reviews remain pending because key possession does not prove benign behavior.

This path requires only an Ed25519 signature and the existing authenticated
transport. It does not download artifacts, execute Pyxel workloads, consume a
compute lease, or override local compute policy.



## Resource-lease integrity

Worker resource admission is cumulative across active leases. The coordinator subtracts RAM and thread reservations for the worker's running tasks and caps new claims against registered static/safe limits. A worker therefore cannot obtain multiple individually-valid leases that collectively exceed its advertised safe budget.


## Pyxel-native agent authorization boundary

The Agent Module now has a default-deny capability layer separate from VM execution. Grants contain exact capability/action names and may require explicit approval. The installed grant table is integrity-checked inside the ABI capability region before decisions are made.

For an allowed static grant, the runtime forces an exact barrier checkpoint and binds the proposed action to:

- canonical argument SHA-256;
- VM generation;
- VM checkpoint hash;
- exact committed PNG artifact SHA-256.

The resulting domain-separated action hash is suitable for a later Axiomatic Runtime approval artifact. The capability gateway does not invoke tools, shell commands, network operations, or arbitrary code itself.

A generic evaluator is fail-closed and cannot convert an `approval_required` grant into `allow`. Explicit approval verification remains a separate future step.

## Provenance limitations

The in-frame provenance journal is bounded and hash chained. It detects retained-window corruption and records agent execution/mailbox/capability events, but it is not an append-only external audit service and cannot reconstruct history that has rolled out of the finite ring. Full deterministic crash replay requires a separate durable event/WAL layer.
