"""
ZDX Authentication Pipeline with DoS Protection and Ed25519 Asymmetric Signatures.

Implements a hardened authentication pipeline that verifies cryptographic identity
using Ed25519 public-key cryptography BEFORE expensive state operations to prevent
denial-of-service attacks.

Pipeline order (DoS-resistant):
1. Protocol version sanity check (cheap)
2. Timestamp sanity check (cheap)
3. Ed25519 signature verification (cryptographic, cheap relative to state ops)
4. Revocation check (in-memory lookup)
5. Enrollment state check
6. ReplayGuard/Sequence validation (stateful, expensive)
7. Dispatch

This ordering ensures:
- Forged messages are rejected before consuming state tracking resources
- Cryptographic identity verification occurs early using Ed25519
- Non-repudiation: signers cannot deny signing (asymmetric proof)
- Denial-of-service resistance against unauthenticated peers
- Rate limiting/admission controls applied only to verified peers

Integration with Karma System:
- Signature verification failures report to karma system
- ReplayGuardState tracks per-peer behavior for karma feedback
- Suspicious patterns can trigger karma penalties

See SECURITY_MODEL.md and TODO.md for design rationale.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional
from collections import defaultdict


# Configuration constants
MAX_MESSAGE_AGE = 60.0  # seconds
SEQUENCE_WINDOW = 100  # max sequence numbers to track per peer
RATE_LIMIT_WINDOW = 10.0  # seconds
RATE_LIMIT_MAX_MESSAGES = 100  # messages per window per peer


@dataclass
class AuthenticationError(Exception):
    """Raised when authentication fails at any stage."""
    stage: str
    reason: str
    peer_id: str = ""

    def __str__(self):
        return f"AuthenticationError@{self.stage}: {self.reason} (peer: {self.peer_id})"


@dataclass
class ReplayGuardState:
    """Tracks per-peer replay protection state."""
    peer_id: str
    last_sequence: int = -1
    seen_sequences: set = field(default_factory=set)
    last_message_time: float = 0.0
    message_count: int = 0
    rate_limit_window_start: float = 0.0

    def check_and_update_rate_limit(self) -> bool:
        """Returns True if message is within rate limits."""
        now = time.time()

        # Start new window
        if now - self.rate_limit_window_start > RATE_LIMIT_WINDOW:
            self.rate_limit_window_start = now
            self.message_count = 0

        self.message_count += 1
        return self.message_count <= RATE_LIMIT_MAX_MESSAGES

    def check_sequence(self, sequence: int) -> bool:
        """Returns True if sequence is valid (new, not replayed)."""
        if sequence <= self.last_sequence:
            return False  # Out of order or duplicate
        if sequence in self.seen_sequences:
            return False  # Replay detected
        if sequence > self.last_sequence + SEQUENCE_WINDOW:
            # Sequence gap too large; clear old window
            self.seen_sequences.clear()
        self.seen_sequences.add(sequence)
        self.last_sequence = sequence
        return True


class ZDXAuthenticationPipeline:
    """
    Hardened authentication pipeline with DoS resistance using Ed25519.

    Uses public-key cryptography (Ed25519) for message authentication instead of
    shared HMAC secrets. This provides:
    - Non-repudiation (signer cannot deny signing)
    - Better scalability (no shared secrets per peer)
    - Industry standard (RFC 8032)
    - Asymmetric trust model (public key known, private key secret)

    Usage:
        pipeline = ZDXAuthenticationPipeline(
            verify_signature=ed25519_signer.verify_message,
            check_revocation=revocation_list.is_revoked,
            check_enrollment=enrollment_registry.check_status,
            dispatch_handler=message_dispatcher,
        )

        try:
            pipeline.process_message(
                peer_id="node_abc",
                protocol_version=1,
                timestamp=time.time(),
                signature=base64_ed25519_signature,
                payload={"data": ...},
                sequence=42
            )
        except AuthenticationError as e:
            # Handle auth failure, report to karma system
            karma_system.report_event(e.peer_id, ...)
    """

    def __init__(
        self,
        verify_signature: Callable[[str, dict, str], bool],
        check_revocation: Callable[[str], bool],
        check_enrollment: Callable[[str], tuple[bool, Optional[dict]]],
        dispatch_handler: Callable[[str, dict], None],
    ):
        """
        Initialize authentication pipeline with Ed25519 verification.

        Args:
            verify_signature: callable(peer_id, payload, signature_b64) -> bool
                Should use Ed25519Signer.verify_message() or equivalent
                Signature is base64-encoded Ed25519 signature (64 bytes)
                
            check_revocation: callable(peer_id) -> bool (True = revoked, reject)
                
            check_enrollment: callable(peer_id) -> (is_enrolled, enrollment_state)
                
            dispatch_handler: callable(peer_id, payload) -> None
        """
        self.verify_signature = verify_signature
        self.check_revocation = check_revocation
        self.check_enrollment = check_enrollment
        self.dispatch_handler = dispatch_handler

        # Per-peer replay and rate-limit state
        self._replay_guards: dict[str, ReplayGuardState] = defaultdict(
            lambda: ReplayGuardState(peer_id="")
        )

    def process_message(
        self,
        peer_id: str,
        protocol_version: int,
        timestamp: float,
        signature: str,
        payload: dict,
        sequence: Optional[int] = None,
    ) -> None:
        """
        Process an incoming message through the authentication pipeline.

        DoS-resistant order:
        1. Protocol version validation
        2. Timestamp sanity check
        3. Ed25519 signature verification (cryptographic)
        4. Revocation check
        5. Enrollment validation
        6. Rate limiting / Replay protection
        7. Dispatch

        Args:
            peer_id: Source node ID
            protocol_version: Protocol version
            timestamp: Message timestamp (seconds since epoch)
            signature: Base64-encoded Ed25519 signature
            payload: Message payload dict
            sequence: Optional sequence number for replay protection

        Raises:
            AuthenticationError if any stage fails
        """
        try:
            # Stage 1: Protocol version sanity check (cheap)
            self._validate_protocol_version(peer_id, protocol_version)

            # Stage 2: Timestamp sanity check (cheap)
            self._validate_timestamp(peer_id, timestamp)

            # Stage 3: Ed25519 signature verification (cryptographic, reject forgeries early)
            # This is the critical DoS defense: reject forged messages BEFORE
            # consuming state tracking resources
            self._verify_ed25519_signature(peer_id, signature, payload)

            # Stage 4: Revocation check (in-memory lookup)
            self._check_revoked(peer_id)

            # Stage 5: Enrollment validation
            enrollment_state = self._check_enrollment(peer_id)

            # Stage 6: Rate limiting and replay protection (stateful, expensive)
            # Only performed AFTER cryptographic identity is verified
            self._check_rate_limit(peer_id)
            if sequence is not None:
                self._check_replay(peer_id, sequence)

            # Stage 7: Dispatch to handler
            self.dispatch_handler(peer_id, payload)

        except AuthenticationError:
            raise

    def _validate_protocol_version(self, peer_id: str, protocol_version: int) -> None:
        """Stage 1: Validate protocol version."""
        SUPPORTED_VERSIONS = {1}
        if protocol_version not in SUPPORTED_VERSIONS:
            raise AuthenticationError(
                stage="protocol_version",
                reason=f"unsupported version {protocol_version}",
                peer_id=peer_id,
            )

    def _validate_timestamp(self, peer_id: str, timestamp: float) -> None:
        """Stage 2: Validate timestamp is within acceptable range."""
        now = time.time()
        age = abs(now - timestamp)
        if age > MAX_MESSAGE_AGE:
            raise AuthenticationError(
                stage="timestamp",
                reason=f"message age {age:.1f}s exceeds maximum {MAX_MESSAGE_AGE}s",
                peer_id=peer_id,
            )

    def _verify_ed25519_signature(
        self, peer_id: str, signature: str, payload: dict
    ) -> bool:
        """
        Stage 3: Verify Ed25519 cryptographic signature.

        This is the critical DoS defense: reject forged messages BEFORE
        consuming state tracking resources.

        Uses Ed25519 public-key cryptography (asymmetric):
        - Signature is base64-encoded 64-byte Ed25519 signature
        - Verification uses peer's public key (shared during enrollment)
        - Non-repudiation: signer cannot deny signing

        Args:
            peer_id: Peer node ID
            signature: Base64-encoded Ed25519 signature
            payload: Message payload dict

        Returns:
            True if signature is valid

        Raises:
            AuthenticationError if signature verification fails
        """
        if not self.verify_signature(peer_id, payload, signature):
            raise AuthenticationError(
                stage="signature_verification",
                reason="Ed25519 signature verification failed (forged or modified message)",
                peer_id=peer_id,
            )
        return True

    def _check_revoked(self, peer_id: str) -> bool:
        """Stage 4: Check if peer is in revocation registry."""
        if self.check_revocation(peer_id):
            raise AuthenticationError(
                stage="revocation_check",
                reason="peer is revoked (removed from network)",
                peer_id=peer_id,
            )
        return True

    def _check_enrollment(self, peer_id: str) -> Optional[dict]:
        """Stage 5: Check enrollment status."""
        is_enrolled, enrollment_state = self.check_enrollment(peer_id)
        if not is_enrolled:
            raise AuthenticationError(
                stage="enrollment_check",
                reason="peer is not enrolled (public key not registered)",
                peer_id=peer_id,
            )
        return enrollment_state

    def _check_rate_limit(self, peer_id: str) -> bool:
        """
        Stage 6a: Check rate limiting.

        Only performed AFTER cryptographic identity verification.
        This prevents unauthenticated peers from consuming rate-limit state.

        Returns:
            True if within rate limits

        Raises:
            AuthenticationError if rate limit exceeded
        """
        guard = self._replay_guards[peer_id]
        guard.peer_id = peer_id
        if not guard.check_and_update_rate_limit():
            raise AuthenticationError(
                stage="rate_limit",
                reason=f"peer exceeds rate limit ({RATE_LIMIT_MAX_MESSAGES} messages per {RATE_LIMIT_WINDOW}s)",
                peer_id=peer_id,
            )
        return True

    def _check_replay(self, peer_id: str, sequence: int) -> bool:
        """
        Stage 6b: Check replay protection.

        Only performed AFTER cryptographic identity verification.
        Prevents forged messages from consuming replay state.

        Returns:
            True if sequence is valid (not replayed)

        Raises:
            AuthenticationError if replay detected
        """
        guard = self._replay_guards[peer_id]
        guard.peer_id = peer_id
        if not guard.check_sequence(sequence):
            raise AuthenticationError(
                stage="replay_protection",
                reason=f"invalid or replayed sequence number {sequence}",
                peer_id=peer_id,
            )
        return True

    def get_replay_state(self, peer_id: str) -> ReplayGuardState:
        """Get current replay guard state for a peer (for testing/monitoring)."""
        return self._replay_guards.get(
            peer_id, ReplayGuardState(peer_id=peer_id)
        )

    def clear_replay_state(self, peer_id: str) -> None:
        """Clear replay state for a peer (e.g., on revocation)."""
        if peer_id in self._replay_guards:
            del self._replay_guards[peer_id]
