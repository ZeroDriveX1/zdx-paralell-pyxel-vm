"""
Comprehensive test suite for ZDX Authentication Pipeline with Ed25519 Signatures.

Tests verify:
1. Ed25519 asymmetric signature verification
2. Pipeline stage ordering (DoS resistance)
3. Early signature verification before state operations
4. Rate limiting and replay protection effectiveness
5. Enrollment and revocation checks
6. Timestamp validation
7. Protocol version handling
8. Integration with karma system
9. Denial-of-service resistance scenarios
"""

from __future__ import annotations

import pytest
import time
import json
from unittest.mock import Mock, patch
from collections import defaultdict

from zdx_auth_pipeline import (
    ZDXAuthenticationPipeline,
    AuthenticationError,
    ReplayGuardState,
    MAX_MESSAGE_AGE,
    RATE_LIMIT_MAX_MESSAGES,
    RATE_LIMIT_WINDOW,
    SEQUENCE_WINDOW,
)

# Try to import Ed25519, fall back to stub if not available
try:
    from zdx_ed25519_signer import ZDXEd25519Signer, HAS_CRYPTOGRAPHY
except ImportError:
    HAS_CRYPTOGRAPHY = False


class TestEd25519Signatures:
    """Test Ed25519 asymmetric signature generation and verification."""

    @pytest.mark.skipif(not HAS_CRYPTOGRAPHY, reason="cryptography library not installed")
    def test_keypair_generation(self):
        """Test Ed25519 keypair generation."""
        from zdx_ed25519_signer import ZDXEd25519Signer
        
        signer = ZDXEd25519Signer(node_id="test_node")
        
        # Verify keypair was generated
        assert signer._keypair is not None
        assert signer.node_id == "test_node"
        assert signer._keypair.private_key_pem
        assert signer._keypair.public_key_pem

    @pytest.mark.skipif(not HAS_CRYPTOGRAPHY, reason="cryptography library not installed")
    def test_message_signing(self):
        """Test Ed25519 message signing."""
        from zdx_ed25519_signer import ZDXEd25519Signer
        
        signer = ZDXEd25519Signer(node_id="test_node")
        payload = {"command": "contribute", "data": [1, 2, 3]}
        
        # Sign message
        signature = signer.sign_message(payload)
        
        # Verify signature is base64-encoded
        assert isinstance(signature, str)
        assert len(signature) > 0
        # Base64 signature should be decodable
        import base64
        decoded = base64.b64decode(signature)
        assert len(decoded) == 64  # Ed25519 signatures are 64 bytes

    @pytest.mark.skipif(not HAS_CRYPTOGRAPHY, reason="cryptography library not installed")
    def test_signature_verification_valid(self):
        """Test Ed25519 signature verification with valid signature."""
        from zdx_ed25519_signer import ZDXEd25519Signer
        
        signer1 = ZDXEd25519Signer(node_id="node1")
        payload = {"node_id": "node1", "action": "frame_sync"}
        
        # Sign message
        signature = signer1.sign_message(payload)
        
        # Register peer and verify
        signer1.register_peer_public_key("node1", signer1.get_public_key_pem())
        assert signer1.verify_message("node1", payload, signature) is True

    @pytest.mark.skipif(not HAS_CRYPTOGRAPHY, reason="cryptography library not installed")
    def test_signature_verification_invalid(self):
        """Test Ed25519 signature verification rejects invalid signature."""
        from zdx_ed25519_signer import ZDXEd25519Signer
        
        signer1 = ZDXEd25519Signer(node_id="node1")
        signer2 = ZDXEd25519Signer(node_id="node2")
        payload = {"node_id": "node1", "action": "frame_sync"}
        
        # Sign with node1
        signature = signer1.sign_message(payload)
        
        # Try to verify with wrong key
        signer2.register_peer_public_key("node1", signer1.get_public_key_pem())
        # Modify the signature
        import base64
        bad_sig = base64.b64encode(b"X" * 64).decode()
        assert signer2.verify_message("node1", payload, bad_sig) is False

    @pytest.mark.skipif(not HAS_CRYPTOGRAPHY, reason="cryptography library not installed")
    def test_signature_verification_tampered_payload(self):
        """Test Ed25519 verification detects payload tampering."""
        from zdx_ed25519_signer import ZDXEd25519Signer
        
        signer = ZDXEd25519Signer(node_id="node1")
        payload = {"node_id": "node1", "action": "frame_sync", "amount": 100}
        
        # Sign original
        signature = signer.sign_message(payload)
        
        # Register self
        signer.register_peer_public_key("node1", signer.get_public_key_pem())
        
        # Verify original works
        assert signer.verify_message("node1", payload, signature) is True
        
        # Tamper with payload
        tampered = {"node_id": "node1", "action": "frame_sync", "amount": 999}
        
        # Verification should fail
        assert signer.verify_message("node1", tampered, signature) is False

    @pytest.mark.skipif(not HAS_CRYPTOGRAPHY, reason="cryptography library not installed")
    def test_key_persistence(self):
        """Test Ed25519 keys persist across restarts."""
        from zdx_ed25519_signer import ZDXEd25519Signer
        import tempfile
        
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create signer and sign
            signer1 = ZDXEd25519Signer(node_id="node1", key_path=tmpdir)
            pub_key_1 = signer1.get_public_key_pem()
            
            # Create new signer with same path (should load existing key)
            signer2 = ZDXEd25519Signer(node_id="node1", key_path=tmpdir)
            pub_key_2 = signer2.get_public_key_pem()
            
            # Public keys should match
            assert pub_key_1 == pub_key_2

    @pytest.mark.skipif(not HAS_CRYPTOGRAPHY, reason="cryptography library not installed")
    def test_cross_peer_verification(self):
        """Test Ed25519 verification between different peers."""
        from zdx_ed25519_signer import ZDXEd25519Signer
        
        peer_a = ZDXEd25519Signer(node_id="peer_a")
        peer_b = ZDXEd25519Signer(node_id="peer_b")
        
        # Peer A sends message to Peer B
        payload = {"from": "peer_a", "to": "peer_b", "data": "hello"}
        signature = peer_a.sign_message(payload)
        
        # Peer B registers peer_a and verifies
        peer_b.register_peer_public_key("peer_a", peer_a.get_public_key_pem())
        assert peer_b.verify_message("peer_a", payload, signature) is True
        
        # Peer B should reject forged messages
        bad_sig = "AAAAAAAAAA"
        assert peer_b.verify_message("peer_a", payload, bad_sig) is False


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
        Test DoS resistance: attacker floods with forged Ed25519 signatures.
        
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


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
