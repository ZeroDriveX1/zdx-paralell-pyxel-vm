"""
ZDX Authentication Pipeline with DoS Protection.

Implements a hardened authentication pipeline that verifies cryptographic identity
BEFORE expensive state operations to prevent denial-of-service attacks.

Pipeline order (DoS-resistant):
1. Protocol version sanity check (cheap)
2. Timestamp sanity check (cheap)
3. Signature verification (cryptographic, cheap relative to state ops)
4. Revocation check (in-memory lookup)
5. Enrollment state check
6. ReplayGuard/Sequence validation (stateful, expensive)
7. Dispatch

This ordering ensures:
- Forged messages are rejected before consuming state tracking resources
- Cryptographic identity verification occurs early
- Denial-of-service resistance against unauthenticated peers
- Rate limiting/admission controls can be added at dispatch

See SECURITY_RELEASE_GATES.md and TODO.md for security review requirements.
"""

from __future__ import annotations

import time
import hmac
import hashlib
from dataclasses import dataclass, field
from typing import Callable, Optional, Tuple
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
class AuthenticationContext:
    """Holds authentication state for a message."""
    peer_id: str
    protocol_version: int
    timestamp: float
    signature: str
    message_payload: dict
    enrollment_state: Optional[dict] = None
    verified: bool = False


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
    Hardened authentication pipeline with DoS resistance.

    Usage:
        pipeline = ZDXAuthenticationPipeline(
            verify_signature=my_verify_func,
            check_revocation=my_revocation_func,
            check_enrollment=my_enrollment_func,
            dispatch_handler=my_dispatch_func,
        )

        try:
            pipeline.process_message(peer_id, protocol_version, timestamp, signature, payload)
        except AuthenticationError as e:
            # Handle auth failure
            pass
    """

    def __init__(
        self,
        verify_signature: Callable[[str, str, dict], bool],
        check_revocation: Callable[[str], bool],
        check_enrollment: Callable[[str], Tuple[bool, Optional[dict]]],
        dispatch_handler: Callable[[str, dict], None],
    ):
        """
        Initialize authentication pipeline.

        Args:
            verify_signature: callable(peer_id, signature, payload) -> bool
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
        3. Signature verification (cryptographic)
        4. Revocation check
        5. Enrollment validation
        6. Rate limiting / Replay protection
        7. Dispatch

        Raises AuthenticationError if any stage fails.
        """
        try:
            # Stage 1: Protocol version sanity check (cheap)
            self._validate_protocol_version(peer_id, protocol_version)

            # Stage 2: Timestamp sanity check (cheap)
            self._validate_timestamp(peer_id, timestamp)

            # Stage 3: Signature verification (cryptographic, reject forgeries early)
            self._verify_signature(peer_id, signature, payload)

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

    def _verify_signature(self, peer_id: str, signature: str, payload: dict) -> bool:
        """
        Stage 3: Verify cryptographic signature.

        This is the critical DoS defense: reject forged messages BEFORE
        consuming state tracking resources.

        Returns True if signature is valid.
        Raises AuthenticationError if signature verification fails.
        """
        if not self.verify_signature(peer_id, signature, payload):
            raise AuthenticationError(
                stage="signature_verification",
                reason="cryptographic signature verification failed",
                peer_id=peer_id,
            )
        return True

    def _check_revoked(self, peer_id: str) -> bool:
        """Stage 4: Check if peer is in revocation registry."""
        if self.check_revocation(peer_id):
            raise AuthenticationError(
                stage="revocation_check",
                reason="peer is revoked",
                peer_id=peer_id,
            )
        return True

    def _check_enrollment(self, peer_id: str) -> Optional[dict]:
        """Stage 5: Check enrollment status."""
        is_enrolled, enrollment_state = self.check_enrollment(peer_id)
        if not is_enrolled:
            raise AuthenticationError(
                stage="enrollment_check",
                reason="peer is not enrolled",
                peer_id=peer_id,
            )
        return enrollment_state

    def _check_rate_limit(self, peer_id: str) -> bool:
        """
        Stage 6a: Check rate limiting.

        Only performed AFTER cryptographic identity verification.
        This prevents unauthenticated peers from consuming rate-limit state.
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


class SignatureVerifier:
    """Helper for deterministic signature verification."""

    @staticmethod
    def compute_message_hash(payload: dict) -> str:
        """Compute deterministic SHA256 hash of payload."""
        import json
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    @staticmethod
    def verify_hmac_signature(
        shared_secret: str, payload: dict, provided_signature: str
    ) -> bool:
        """
        Verify HMAC-SHA256 signature.

        For testing and development. Production should use Ed25519 asymmetric signatures.
        """
        import json
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        expected_sig = hmac.new(
            shared_secret.encode(), canonical.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected_sig, provided_signature)


# Example usage for testing
def create_example_pipeline():
    """Create an example pipeline with stub implementations."""

    def verify_sig(peer_id: str, sig: str, payload: dict) -> bool:
        # Stub: always succeed in demo
        return True

    def check_revoked(peer_id: str) -> bool:
        # Stub: never revoked
        return False

    def check_enrolled(peer_id: str) -> Tuple[bool, Optional[dict]]:
        # Stub: always enrolled
        return True, {"status": "active"}

    def dispatch(peer_id: str, payload: dict) -> None:
        print(f"[DISPATCH] Message from {peer_id}: {payload}")

    return ZDXAuthenticationPipeline(
        verify_signature=verify_sig,
        check_revocation=check_revoked,
        check_enrollment=check_enrolled,
        dispatch_handler=dispatch,
    )
