# ZDX Security Model

## Authenticated network boundary

The coordinator accepts only the canonical `ZDXMessage` envelope. Its Ed25519 signature covers the protocol version, message type, sender identity, session ID, sequence, timestamp, nonce, request ID, key version, payload checksum, and payload. Validation fails before dispatch when any field is missing, malformed, unsupported, stale, unknown, unsigned, or cryptographically invalid.

Node IDs are SHA-256 digests of their Ed25519 public keys. Enrollment is explicit public-key pinning. A coordinator cannot silently accept a self-declared identity, and a client must pin the coordinator key before connecting.

## Session authentication

Session establishment is a signed two-challenge exchange. The enrolled node sends a fresh client challenge; the coordinator proves receipt and sends its challenge; the node signs that challenge; and the coordinator activates the expiring session and signs its acknowledgement.

Only one session for a node is active in a registry. Reconnect replaces the old session. Application sequences are strict and monotonic. Nonces are single-use within their bounded window, timestamps allow at most 60 seconds of skew/age, and expired sessions are rejected and cleaned up. Key rotation is versioned and requires explicit authorization.

## Coordinator boundary

Registration binds the signed sender identity to its authenticated session. Duplicate node IDs using another key, cross-identity session reuse, heartbeat activity on a replaced session, and mismatched identity claims are rejected. Registry and replay state are lock-protected; stale nodes can be removed deterministically.

## Remaining production controls

Ed25519 authenticates application messages while required TLS 1.3 encrypts production connections. Remaining deployment work includes durable/recoverable trust and revocation configuration, connection quotas and handshake timeouts, monitoring, a coordinated clock policy, physical multi-host validation, and workload authorization before remote execution.

The coordinator does not execute received programs. PNG storage is not encryption or sender authentication.

## Persistent-state integrity

Managed state is written through a checksummed, versioned envelope under a bounded process lock. Writers commit a fully flushed same-directory temporary file through atomic replacement and retain the prior generation for rollback. Readers verify format, schema, compatibility metadata, and checksum before exposing data. Invalid generations are quarantined; a verified backup is restored when available, otherwise corruption is reported rather than silently accepted.

Private Ed25519 PEM data is the only necessary secret persisted outside the JSON envelope. It is atomically written with owner-only permissions; checksummed metadata stores only public identifiers, algorithms, and secret filenames. Trust, revocation, session audit, coordinator, and registry metadata use the canonical store. Active sessions are deliberately not resumed after restart.

The lock implementation relies on kernel-managed POSIX `fcntl` locks, which are released on process exit. Distributed filesystem lock and fsync guarantees are deployment properties and have not been validated in this environment.

## Operational failure validation

Deterministic injection points exercise failures after temporary-file fsync, after backup rotation, before final replacement, and before registry persistence. Local tests verify rollback to the last valid generation without lingering temporary files. Malformed network, JSON state, migration, PNG metadata, transaction envelope, and identity inputs fail closed in bounded fuzz campaigns.

Metrics export contains aggregate counts, latency, resource usage, and uptime only; it must not include keys, payloads, identities, signatures, or other secrets. Pass 13 results do not cover hostile kernel/filesystem behavior, physical power loss, or distributed lock managers. TLS transport validation is documented separately under Pass 14.

## Transport security (Pass 14)

Production coordinator/client constructors require TLS configuration unless tests explicitly opt into insecure transport. Contexts require TLS 1.3, CA verification, peer certificates, and client hostname verification. Optional CRLs reject revoked certificates when OpenSSL chain verification supports the configured CRL. TLS authenticates the transport; canonical signed envelopes, pinned Ed25519 identities, sessions, nonces, sequences, timestamps, and checksums remain mandatory application controls.

Server certificate context replacement affects new connections; existing connections must expire or reconnect. Certificate issuance, protected distribution, fleet-wide CRL refresh, and rotation orchestration remain operator responsibilities. There is no OCSP client or automated CA enrollment. Connection quotas and pre-authentication resource controls remain deployment limitations.

## Local inference boundary (Pass 15)

The Ollama provider permits plaintext HTTP only to loopback addresses and requires HTTPS for non-loopback endpoints. Endpoint URLs containing credentials are rejected. Standard HTTPS certificate and hostname verification applies. Request duration, retry count, backoff, pool size, generated-token count, temperature, and response bytes are bounded through environment configuration. Provider failures expose structured codes without response bodies or configuration secrets.

Mission prompts and model responses are intentionally stored in checksummed mission history and may optionally be mirrored into PNG memory. Operators must not submit credentials or private keys as mission content; PNG encoding is not confidentiality. The default verifier establishes only that output is non-empty. Factual correctness, authorization, prompt-injection resistance, and policy compliance require an injected mission-specific verifier and trusted memory inputs.
