"""
Comprehensive test suite for ZDX Authentication Pipeline with DoS Protection.

Tests verify:
1. Pipeline stage ordering (DoS resistance)
2. Early signature verification before state operations
3. Rate limiting and replay protection effectiveness
4. Enrollment and revocation checks
5. Timestamp validation
6. Protocol version handling
7. Integration with karma system
8. Denial-of-service resistance scenarios
"""

from __future__ import annotations

import pytest
import time
import hmac
import hashlib
import json
from unittest.mock import Mock, MagicMock, patch
from collections import defaultdict

from zdx_auth_pipeline import (
    ZDXAuthenticationPipeline,
    AuthenticationError,
    ReplayGuardState,
    SignatureVerifier,
    MAX_MESSAGE_AGE,
    RATE_LIMIT_MAX_MESSAGES,
    RATE_LIMIT_WINDOW,
    SEQUENCE_WINDOW,
)


class TestSignatureVerifier:
    """Test signature verification utilities."""

    def test_compute_message_hash(self):
        """Test deterministic message hashing."""
        payload = {"command": "contribute", "data": [1, 2, 3]}
        hash1 = SignatureVerifier.compute_message_hash(payload)
        hash2 = SignatureVerifier.compute_message_hash(payload)

        # Same payload must produce same hash
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 hex = 64 chars

    def test_compute_message_hash_order_independent(self):
        """Test that key order doesn't affect hash."""
        payload1 = {"a": 1, "b": 2}
        payload2 = {"b": 2, "a": 1}

        hash1 = SignatureVerifier.compute_message_hash(payload1)
        hash2 = SignatureVerifier.compute_message_hash(payload2)

        # Different key order should produce same hash (JSON canonical form)
        assert hash1 == hash2

    def test_verify_hmac_signature_valid(self):
        """Test HMAC signature verification with valid signature."""
        secret = "shared_secret_key"
        payload = {"node_id": "node123", "action": "frame_sync"}

        # Generate valid signature
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        signature = hmac.new(
            secret.encode(), canonical.encode(), hashlib.sha256
        ).hexdigest()

        # Verify it validates
        assert SignatureVerifier.verify_hmac_signature(secret, payload, signature)

    def test_verify_hmac_signature_invalid(self):
        """Test HMAC signature verification with invalid signature."""
        secret = "shared_secret_key"
        payload = {"node_id": "node123", "action": "frame_sync"}

        # Try with wrong secret
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        wrong_signature = hmac.new(
            "wrong_secret".encode(), canonical.encode(), hashlib.sha256
        ).hexdigest()

        # Should fail verification
        assert not SignatureVerifier.verify_hmac_signature(secret, payload, wrong_signature)

    def test_verify_hmac_signature_tampered_payload(self):
        """Test HMAC verification detects payload tampering."""
        secret = "shared_secret_key"
        payload = {"node_id": "node123", "action": "frame_sync"}

        # Generate valid signature
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        signature = hmac.new(
            secret.encode(), canonical.encode(), hashlib.sha256
        ).hexdigest()

        # Tamper with payload
        payload["action"] = "malicious_action"

        # Should fail verification
        assert not SignatureVerifier.verify_hmac_signature(secret, payload, signature)


class TestReplayGuardState:
    """Test replay protection state tracking."""

    def test_rate_limit_initialization(self):
        """Test rate limit state initializes correctly."""
        guard = ReplayGuardState(peer_id="test_peer")
        assert guard.peer_id == "test_peer"
        assert guard.message_count == 0
        assert guard.last_sequence == -1

    def test_rate_limit_accepts_within_limit(self):
        """Test rate limit accepts messages within limit."""
        guard = ReplayGuardState(peer_id="test_peer")
        guard.rate_limit_window_start = time.time()

        for i in range(RATE_LIMIT_MAX_MESSAGES):
            assert guard.check_and_update_rate_limit() is True

    def test_rate_limit_rejects_exceeding_limit(self):
        """Test rate limit rejects messages exceeding limit."""
        guard = ReplayGuardState(peer_id="test_peer")
        guard.rate_limit_window_start = time.time()

        # Fill the limit
        for i in range(RATE_LIMIT_MAX_MESSAGES):
            guard.check_and_update_rate_limit()

        # Next message should fail
        assert guard.check_and_update_rate_limit() is False

    def test_rate_limit_window_reset(self):
        """Test rate limit window resets after timeout."""
        guard = ReplayGuardState(peer_id="test_peer")
        guard.rate_limit_window_start = time.time() - (RATE_LIMIT_WINDOW + 1)

        # After window expired, should accept again
        assert guard.check_and_update_rate_limit() is True

    def test_sequence_validation_monotonic(self):
        """Test sequence numbers must be monotonically increasing."""
        guard = ReplayGuardState(peer_id="test_peer")

        assert guard.check_sequence(1) is True  # First sequence accepted
        assert guard.check_sequence(2) is True  # Increasing accepted
        assert guard.check_sequence(2) is False  # Duplicate rejected
        assert guard.check_sequence(1) is False  # Out of order rejected

    def test_sequence_gap_tolerance(self):
        """Test sequence numbers tolerate gaps within window."""
        guard = ReplayGuardState(peer_id="test_peer")

        assert guard.check_sequence(1) is True
        assert guard.check_sequence(5) is True  # Gap within window
        assert guard.check_sequence(3) is False  # Out of order
        assert guard.check_sequence(10) is True  # Another gap

    def test_sequence_large_gap_clears_history(self):
        """Test large sequence gaps clear old history."""
        guard = ReplayGuardState(peer_id="test_peer")

        # Build up sequence history
        for i in range(1, 20):
            guard.check_sequence(i)

        assert len(guard.seen_sequences) == 19

        # Huge gap clears history
        huge_next = guard.last_sequence + SEQUENCE_WINDOW + 100
        assert guard.check_sequence(huge_next) is True

        # History was cleared
        assert len(guard.seen_sequences) == 1


class TestAuthenticationPipelineStages:
    """Test individual authentication pipeline stages."""

    def setup_method(self):
        """Set up test fixtures."""
        self.verify_sig_func = Mock(return_value=True)
        self.revocation_func = Mock(return_value=False)
        self.enrollment_func = Mock(return_value=(True, {"status": "active"}))
        self.dispatch_func = Mock()

        self.pipeline = ZDXAuthenticationPipeline(
            verify_signature=self.verify_sig_func,
            check_revocation=self.revocation_func,
            check_enrollment=self.enrollment_func,
            dispatch_handler=self.dispatch_func,
        )

    def test_protocol_version_validation_pass(self):
        """Test protocol version validation passes for supported version."""
        self.pipeline.process_message(
            peer_id="peer1",
            protocol_version=1,
            timestamp=time.time(),
            signature="valid_sig",
            payload={"data": "test"},
        )
        assert self.dispatch_func.called

    def test_protocol_version_validation_fail(self):
        """Test protocol version validation rejects unsupported version."""
        with pytest.raises(AuthenticationError) as exc_info:
            self.pipeline.process_message(
                peer_id="peer1",
                protocol_version=999,
                timestamp=time.time(),
                signature="valid_sig",
                payload={"data": "test"},
            )

        assert exc_info.value.stage == "protocol_version"
        assert "unsupported version" in exc_info.value.reason

    def test_timestamp_validation_pass(self):
        """Test timestamp validation passes for current timestamp."""
        self.pipeline.process_message(
            peer_id="peer1",
            protocol_version=1,
            timestamp=time.time(),
            signature="valid_sig",
            payload={"data": "test"},
        )
        assert self.dispatch_func.called

    def test_timestamp_validation_fail_too_old(self):
        """Test timestamp validation rejects old messages."""
        old_timestamp = time.time() - (MAX_MESSAGE_AGE + 10)

        with pytest.raises(AuthenticationError) as exc_info:
            self.pipeline.process_message(
                peer_id="peer1",
                protocol_version=1,
                timestamp=old_timestamp,
                signature="valid_sig",
                payload={"data": "test"},
            )

        assert exc_info.value.stage == "timestamp"

    def test_timestamp_validation_fail_future(self):
        """Test timestamp validation rejects future messages."""
        future_timestamp = time.time() + (MAX_MESSAGE_AGE + 10)

        with pytest.raises(AuthenticationError) as exc_info:
            self.pipeline.process_message(
                peer_id="peer1",
                protocol_version=1,
                timestamp=future_timestamp,
                signature="valid_sig",
                payload={"data": "test"},
            )

        assert exc_info.value.stage == "timestamp"

    def test_signature_verification_pass(self):
        """Test signature verification passes for valid signature."""
        self.verify_sig_func.return_value = True

        self.pipeline.process_message(
            peer_id="peer1",
            protocol_version=1,
            timestamp=time.time(),
            signature="valid_sig",
            payload={"data": "test"},
        )

        assert self.dispatch_func.called
        self.verify_sig_func.assert_called_once()

    def test_signature_verification_fail(self):
        """Test signature verification rejects forged signature."""
        self.verify_sig_func.return_value = False

        with pytest.raises(AuthenticationError) as exc_info:
            self.pipeline.process_message(
                peer_id="peer1",
                protocol_version=1,
                timestamp=time.time(),
                signature="forged_sig",
                payload={"data": "test"},
            )

        assert exc_info.value.stage == "signature_verification"
        assert not self.dispatch_func.called

    def test_revocation_check_pass(self):
        """Test revocation check passes for non-revoked peer."""
        self.revocation_func.return_value = False

        self.pipeline.process_message(
            peer_id="peer1",
            protocol_version=1,
            timestamp=time.time(),
            signature="valid_sig",
            payload={"data": "test"},
        )

        assert self.dispatch_func.called

    def test_revocation_check_fail(self):
        """Test revocation check rejects revoked peer."""
        self.revocation_func.return_value = True

        with pytest.raises(AuthenticationError) as exc_info:
            self.pipeline.process_message(
                peer_id="peer1",
                protocol_version=1,
                timestamp=time.time(),
                signature="valid_sig",
                payload={"data": "test"},
            )

        assert exc_info.value.stage == "revocation_check"
        assert not self.dispatch_func.called

    def test_enrollment_check_pass(self):
        """Test enrollment check passes for enrolled peer."""
        self.enrollment_func.return_value = (True, {"status": "active"})

        self.pipeline.process_message(
            peer_id="peer1",
            protocol_version=1,
            timestamp=time.time(),
            signature="valid_sig",
            payload={"data": "test"},
        )

        assert self.dispatch_func.called

    def test_enrollment_check_fail(self):
        """Test enrollment check rejects unenrolled peer."""
        self.enrollment_func.return_value = (False, None)

        with pytest.raises(AuthenticationError) as exc_info:
            self.pipeline.process_message(
                peer_id="peer1",
                protocol_version=1,
                timestamp=time.time(),
                signature="valid_sig",
                payload={"data": "test"},
            )

        assert exc_info.value.stage == "enrollment_check"
        assert not self.dispatch_func.called


class TestAuthenticationPipelineOrdering:
    """Test that pipeline stages execute in DoS-resistant order."""

    def test_signature_verified_before_rate_limit(self):
        """Test that forged messages don't consume rate limit state."""
        verify_sig = Mock(return_value=False)
        revocation_check = Mock(return_value=False)
        enrollment_check = Mock(return_value=(True, {}))
        dispatch = Mock()

        pipeline = ZDXAuthenticationPipeline(
            verify_signature=verify_sig,
            check_revocation=revocation_check,
            check_enrollment=enrollment_check,
            dispatch_handler=dispatch,
        )

        # Send forged message
        with pytest.raises(AuthenticationError):
            pipeline.process_message(
                peer_id="attacker",
                protocol_version=1,
                timestamp=time.time(),
                signature="forged",
                payload={"data": "attack"},
            )

        # Check that rate limit state was NOT created for attacker
        # (because signature check failed before rate limit)
        guard = pipeline.get_replay_state("attacker")
        assert guard.message_count == 0

    def test_signature_verified_before_replay_protection(self):
        """Test that forged messages don't consume sequence state."""
        verify_sig = Mock(return_value=False)
        revocation_check = Mock(return_value=False)
        enrollment_check = Mock(return_value=(True, {}))
        dispatch = Mock()

        pipeline = ZDXAuthenticationPipeline(
            verify_signature=verify_sig,
            check_revocation=revocation_check,
            check_enrollment=enrollment_check,
            dispatch_handler=dispatch,
        )

        # Send forged messages with sequences
        for seq in [1, 2, 3]:
            with pytest.raises(AuthenticationError):
                pipeline.process_message(
                    peer_id="attacker",
                    protocol_version=1,
                    timestamp=time.time(),
                    signature=f"forged_{seq}",
                    payload={"data": "attack"},
                    sequence=seq,
                )

        # Check that sequence state was NOT updated
        guard = pipeline.get_replay_state("attacker")
        assert guard.last_sequence == -1

    def test_revocation_checked_after_signature(self):
        """Test revocation is checked after signature verification."""
        call_order = []

        def verify_sig(peer_id, sig, payload):
            call_order.append("verify_signature")
            return True

        def check_revoked(peer_id):
            call_order.append("check_revocation")
            return False

        def check_enrolled(peer_id):
            call_order.append("check_enrollment")
            return True, {}

        dispatch = Mock()

        pipeline = ZDXAuthenticationPipeline(
            verify_signature=verify_sig,
            check_revocation=check_revoked,
            check_enrollment=check_enrolled,
            dispatch_handler=dispatch,
        )

        pipeline.process_message(
            peer_id="peer1",
            protocol_version=1,
            timestamp=time.time(),
            signature="valid",
            payload={"data": "test"},
        )

        # Verify order
        assert call_order.index("verify_signature") < call_order.index("check_revocation")
        assert call_order.index("check_revocation") < call_order.index("check_enrollment")


class TestRateLimitingDoS:
    """Test rate limiting protects against DoS attacks."""

    def setup_method(self):
        """Set up test fixtures."""
        self.verify_sig = Mock(return_value=True)
        self.revocation = Mock(return_value=False)
        self.enrollment = Mock(return_value=(True, {}))
        self.dispatch = Mock()

        self.pipeline = ZDXAuthenticationPipeline(
            verify_signature=self.verify_sig,
            check_revocation=self.revocation,
            check_enrollment=self.enrollment,
            dispatch_handler=self.dispatch,
        )

    def test_rate_limit_enforced_per_peer(self):
        """Test rate limits are per-peer, not global."""
        # Peer 1 sends max messages
        for i in range(RATE_LIMIT_MAX_MESSAGES):
            self.pipeline.process_message(
                peer_id="peer1",
                protocol_version=1,
                timestamp=time.time(),
                signature="valid",
                payload={"seq": i},
                sequence=i + 1,
            )

        assert self.dispatch.call_count == RATE_LIMIT_MAX_MESSAGES

        # Peer 2 should still be able to send (per-peer limit)
        self.dispatch.reset_mock()
        for i in range(10):
            self.pipeline.process_message(
                peer_id="peer2",
                protocol_version=1,
                timestamp=time.time(),
                signature="valid",
                payload={"seq": i},
                sequence=i + 1,
            )

        assert self.dispatch.call_count == 10

    def test_rate_limit_exceeded_rejected(self):
        """Test messages exceeding rate limit are rejected."""
        # Fill the limit
        for i in range(RATE_LIMIT_MAX_MESSAGES):
            self.pipeline.process_message(
                peer_id="peer1",
                protocol_version=1,
                timestamp=time.time(),
                signature="valid",
                payload={"seq": i},
                sequence=i + 1,
            )

        # Next message should fail
        with pytest.raises(AuthenticationError) as exc_info:
            self.pipeline.process_message(
                peer_id="peer1",
                protocol_version=1,
                timestamp=time.time(),
                signature="valid",
                payload={"seq": RATE_LIMIT_MAX_MESSAGES},
                sequence=RATE_LIMIT_MAX_MESSAGES + 1,
            )

        assert exc_info.value.stage == "rate_limit"


class TestReplayProtectionDoS:
    """Test replay protection prevents replay attacks."""

    def setup_method(self):
        """Set up test fixtures."""
        self.verify_sig = Mock(return_value=True)
        self.revocation = Mock(return_value=False)
        self.enrollment = Mock(return_value=(True, {}))
        self.dispatch = Mock()

        self.pipeline = ZDXAuthenticationPipeline(
            verify_signature=self.verify_sig,
            check_revocation=self.revocation,
            check_enrollment=self.enrollment,
            dispatch_handler=self.dispatch,
        )

    def test_replay_attack_detected(self):
        """Test replayed messages are detected and rejected."""
        payload = {"command": "transfer", "amount": 1000}

        # Send original message
        self.pipeline.process_message(
            peer_id="peer1",
            protocol_version=1,
            timestamp=time.time(),
            signature="valid",
            payload=payload,
            sequence=1,
        )

        assert self.dispatch.call_count == 1

        # Replay the same message
        with pytest.raises(AuthenticationError) as exc_info:
            self.pipeline.process_message(
                peer_id="peer1",
                protocol_version=1,
                timestamp=time.time(),
                signature="valid",
                payload=payload,
                sequence=1,
            )

        assert exc_info.value.stage == "replay_protection"
        # Dispatch should NOT be called for replay
        assert self.dispatch.call_count == 1

    def test_out_of_order_sequence_rejected(self):
        """Test out-of-order sequences are rejected."""
        # Send sequence 3
        with pytest.raises(AuthenticationError):
            self.pipeline.process_message(
                peer_id="peer1",
                protocol_version=1,
                timestamp=time.time(),
                signature="valid",
                payload={"data": "test"},
                sequence=3,
            )

        assert self.dispatch.call_count == 0


class TestDosResistanceScenarios:
    """Test real-world DoS scenarios."""

    def setup_method(self):
        """Set up test fixtures."""
        self.verify_sig = Mock(return_value=True)
        self.revocation = Mock(return_value=False)
        self.enrollment = Mock(return_value=(True, {}))
        self.dispatch = Mock()

        self.pipeline = ZDXAuthenticationPipeline(
            verify_signature=self.verify_sig,
            check_revocation=self.revocation,
            check_enrollment=self.enrollment,
            dispatch_handler=self.dispatch,
        )

    def test_forged_message_spam_dos(self):
        """
        Test DoS resistance: attacker floods with forged signatures.
        
        With proper ordering, forged messages are rejected at signature stage,
        before consuming any state tracking resources.
        """
        self.verify_sig.return_value = False  # Attacker's signatures fail

        # Attacker floods with 1000 forged messages
        rejected_count = 0
        for i in range(1000):
            try:
                self.pipeline.process_message(
                    peer_id="attacker",
                    protocol_version=1,
                    timestamp=time.time(),
                    signature=f"forged_{i}",
                    payload={"data": f"spam_{i}"},
                    sequence=i + 1,
                )
            except AuthenticationError as e:
                if e.stage == "signature_verification":
                    rejected_count += 1

        # All 1000 should be rejected
        assert rejected_count == 1000

        # Most importantly: attacker's state should be minimal
        # (no rate limit state consumed, no sequence tracking)
        attacker_guard = self.pipeline.get_replay_state("attacker")
        assert attacker_guard.message_count == 0
        assert attacker_guard.last_sequence == -1

        # Legitimate peer should not be affected
        self.verify_sig.return_value = True
        for i in range(10):
            self.pipeline.process_message(
                peer_id="legit",
                protocol_version=1,
                timestamp=time.time(),
                signature="valid",
                payload={"data": f"msg_{i}"},
                sequence=i + 1,
            )

        # All 10 should succeed
        assert self.dispatch.call_count == 10

    def test_state_exhaustion_attack_prevented(self):
        """
        Test DoS resistance: attacker tries to exhaust replay guard state.
        
        With old (bad) ordering, huge number of sequence gaps could cause
        memory exhaustion. With DoS-resistant ordering, attacker must pass
        signature check, limiting attack surface.
        """
        # Attacker tries to create huge gaps in sequence numbers
        self.verify_sig.return_value = False

        for i in range(100):
            huge_seq = i * 1000000  # Huge gaps
            try:
                self.pipeline.process_message(
                    peer_id="attacker",
                    protocol_version=1,
                    timestamp=time.time(),
                    signature="forged",
                    payload={"data": "spam"},
                    sequence=huge_seq,
                )
            except AuthenticationError:
                pass  # Expected

        # Attacker's guard should not have consumed memory
        guard = self.pipeline.get_replay_state("attacker")
        assert len(guard.seen_sequences) == 0  # Never got past signature check

    def test_timestamp_validation_dos_attempt(self):
        """Test that old/future timestamps are rejected early."""
        old_ts = time.time() - 1000
        future_ts = time.time() + 1000

        # Old timestamp
        with pytest.raises(AuthenticationError) as exc:
            self.pipeline.process_message(
                peer_id="attacker",
                protocol_version=1,
                timestamp=old_ts,
                signature="valid",
                payload={"data": "spam"},
            )
        assert exc.value.stage == "timestamp"

        # Future timestamp
        with pytest.raises(AuthenticationError) as exc:
            self.pipeline.process_message(
                peer_id="attacker",
                protocol_version=1,
                timestamp=future_ts,
                signature="valid",
                payload={"data": "spam"},
            )
        assert exc.value.stage == "timestamp"

        # No state consumed by attacker
        guard = self.pipeline.get_replay_state("attacker")
        assert guard.message_count == 0


class TestAuthenticationErrorHandling:
    """Test error handling and reporting."""

    def setup_method(self):
        """Set up test fixtures."""
        self.verify_sig = Mock(return_value=True)
        self.revocation = Mock(return_value=False)
        self.enrollment = Mock(return_value=(True, {}))
        self.dispatch = Mock()

        self.pipeline = ZDXAuthenticationPipeline(
            verify_signature=self.verify_sig,
            check_revocation=self.revocation,
            check_enrollment=self.enrollment,
            dispatch_handler=self.dispatch,
        )

    def test_authentication_error_contains_details(self):
        """Test that authentication errors contain helpful details."""
        self.verify_sig.return_value = False

        with pytest.raises(AuthenticationError) as exc_info:
            self.pipeline.process_message(
                peer_id="attacker",
                protocol_version=1,
                timestamp=time.time(),
                signature="bad",
                payload={"data": "test"},
            )

        error = exc_info.value
        assert error.stage == "signature_verification"
        assert error.peer_id == "attacker"
        assert "signature" in error.reason.lower()

    def test_error_string_representation(self):
        """Test error string representation."""
        error = AuthenticationError(
            stage="test_stage",
            reason="test failed",
            peer_id="test_peer",
        )

        error_str = str(error)
        assert "test_stage" in error_str
        assert "test_peer" in error_str
        assert "test failed" in error_str


class TestPipelineIntegration:
    """Integration tests for the full pipeline."""

    def setup_method(self):
        """Set up test fixtures."""
        self.peers = {}
        self.revoked = set()

        def verify_sig(peer_id, sig, payload):
            return sig == f"sig_{peer_id}"

        def check_revoked(peer_id):
            return peer_id in self.revoked

        def check_enrolled(peer_id):
            return peer_id in self.peers, self.peers.get(peer_id, {})

        self.dispatch_calls = []

        def dispatch(peer_id, payload):
            self.dispatch_calls.append((peer_id, payload))

        self.pipeline = ZDXAuthenticationPipeline(
            verify_signature=verify_sig,
            check_revocation=check_revoked,
            check_enrollment=check_enrolled,
            dispatch_handler=dispatch,
        )

        # Register peers
        self.peers["peer1"] = {"status": "active"}
        self.peers["peer2"] = {"status": "active"}

    def test_full_successful_authentication(self):
        """Test complete successful authentication flow."""
        self.pipeline.process_message(
            peer_id="peer1",
            protocol_version=1,
            timestamp=time.time(),
            signature="sig_peer1",
            payload={"command": "contribute"},
            sequence=1,
        )

        assert len(self.dispatch_calls) == 1
        assert self.dispatch_calls[0][0] == "peer1"

    def test_multiple_peers_independent_state(self):
        """Test multiple peers maintain independent state."""
        # Peer 1 sends sequences 1, 2, 3
        for i in range(1, 4):
            self.pipeline.process_message(
                peer_id="peer1",
                protocol_version=1,
                timestamp=time.time(),
                signature="sig_peer1",
                payload={"seq": i},
                sequence=i,
            )

        # Peer 2 sends sequences 1, 2, 3 independently
        for i in range(1, 4):
            self.pipeline.process_message(
                peer_id="peer2",
                protocol_version=1,
                timestamp=time.time(),
                signature="sig_peer2",
                payload={"seq": i},
                sequence=i,
            )

        assert len(self.dispatch_calls) == 6

    def test_unauthenticated_peer_rejected(self):
        """Test unauthenticated peer is rejected at enrollment stage."""
        with pytest.raises(AuthenticationError) as exc:
            self.pipeline.process_message(
                peer_id="unknown_peer",
                protocol_version=1,
                timestamp=time.time(),
                signature="sig_unknown_peer",
                payload={"data": "test"},
            )

        assert exc.value.stage == "enrollment_check"

    def test_revoked_peer_rejected_after_signature(self):
        """Test revoked peer is rejected after signature verification."""
        self.revoked.add("peer1")

        with pytest.raises(AuthenticationError) as exc:
            self.pipeline.process_message(
                peer_id="peer1",
                protocol_version=1,
                timestamp=time.time(),
                signature="sig_peer1",
                payload={"data": "test"},
            )

        assert exc.value.stage == "revocation_check"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
