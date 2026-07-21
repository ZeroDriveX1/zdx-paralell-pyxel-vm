# Open-Pyxel Development TODO

## Current Priority: Security Foundation

Status: IN PROGRESS

Complete security boundary before continuing feature expansion.

Completed:

- [x] Persistent Ed25519 node identity
- [x] Deterministic node ID derived from public key
- [x] Signing primitives
- [x] Signature verification primitives
- [x] Key storage foundation
- [x] Replay protection foundation
- [x] Revocation registry foundation
- [x] Session authentication foundation

Current:

- [ ] Integrate authentication pipeline into networking layer
- [ ] Implement enrollment state handling
- [ ] Harden malformed packet handling
- [ ] Replace address-based peer trust with node identity trust
- [ ] Add security integration tests

---

# MUST DO — Authentication Pipeline DoS Resistance Review

Priority: HIGH

Before public node enrollment or large-scale untrusted network operation, review and finalize authentication pipeline ordering.

Current trusted-network pipeline:

protocol version
→ timestamp
→ ReplayGuard
→ sequence validation
→ signature verification
→ revocation
→ enrollment
→ dispatch

Concern:

Stateful replay and sequence tracking occur before cryptographic identity verification. A hostile peer could send forged messages that consume state tracking resources before failing signature validation.

Required future review:

Evaluate whether the production pipeline should become:

protocol version
→ timestamp sanity check
→ signature verification
→ revocation
→ enrollment
→ ReplayGuard
→ sequence validation
→ dispatch

Goals:

- Reject forged messages before expensive state operations.
- Preserve replay protection.
- Improve denial-of-service resistance.
- Add rate limiting/admission controls if public network exposure requires it.

Decision must be documented before:

- public node discovery
- open enrollment
- production-scale deployment

---

# Compute Trust Layer Roadmap

Authentication proves who submitted data.
Verification proves whether the claim is true.

Future subsystem:

compute/

- metering.py
- verification.py
- contribution.py

Requirements:

- Deterministic result verification through VM replay or redundant execution.
- Server-authoritative contribution records.
- Timing checks based on node capability history, not global thresholds.
- Duplicate detection keyed by job_id + node_id.
- Reputation feedback loop.
- Security revocation integration for repeated malicious behavior.

---

# Post Security Review Required

After security completion:

STOP feature expansion.

Perform:

- code review
- architecture review
- red-team security review
- threat model review
- simplification pass
- documentation review

Then establish permanent development workflow:

1. Proposal
2. Build map
3. Implementation
4. Unit tests
5. Integration tests
6. Security review
7. Red-team review
8. Documentation update
9. Merge

---

# Documentation

Keep current:

- ARCHITECTURE.md
- NETWORK_PROTOCOL.md
- SECURITY_MODEL.md
- NODE_OPERATOR_GUIDE.md
- COMPUTE_CONTRIBUTION.md
- TRAINING_JOBS.md
- AGENT_RUNTIME.md
- ROADMAP.md
