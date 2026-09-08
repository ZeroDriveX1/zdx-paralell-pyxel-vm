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

Authenticated transport is enabled with `ZDXServer(require_auth=True)` and a
trusted public-key mapping. The default unsigned server mode exists for local
development compatibility and is not a security boundary.

## Remaining Security Work

- persistent enrollment and revocation storage
- signed enrollment responses and key-rotation lifecycle
- capability attestation
- encrypted transport (TLS/mTLS)
- workload authorization and quotas
- artifact distribution and result-size limits
- independent protocol fuzzing and penetration testing

The worker does not execute arbitrary Python, shell commands, or user-supplied
code. Its current workload is a local, hash-verified PNG frame. CPU and memory
limits must be enforced by both the JSON policy and the host service manager;
the policy alone cannot provide a hard CPU quota on every operating system.

Security decisions remain modular so deployments can choose appropriate trust levels.
