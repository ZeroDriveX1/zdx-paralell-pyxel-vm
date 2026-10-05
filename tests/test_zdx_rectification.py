import time

import pytest

from zdx_auth_pipeline import AuthenticationError
from zdx_ed25519_signer import ZDXEd25519Signer
from zdx_network import ZDXMessage
from zdx_reattest import ReattestationChallengeStore
from zdx_rectification import RectificationQueue
from zdx_server_core import ZDXServer
from zdx_state import ZDXState


def test_rectification_queue_is_advisory_and_deduplicates(tmp_path):
    queue = RectificationQueue(str(tmp_path / "rectification.json"))
    first = queue.request(
        reporter_node_id="observer",
        target_node_id="peer-a",
        reason_code="suspicious_authenticated_behavior",
        detail="signed behavior needs review",
        now=1000,
    )
    second = queue.request(
        reporter_node_id="observer",
        target_node_id="peer-a",
        reason_code="suspicious_authenticated_behavior",
        detail="same observation repeated",
        now=1061,
    )

    assert first["request_id"] == second["request_id"]
    assert second["status"] == "pending"
    assert second["repeat_count"] == 2
    assert queue.pending_count() == 1


def test_state_finds_only_active_long_authenticated_peers(tmp_path):
    state = ZDXState(str(tmp_path / "state.json"))
    now = time.time()
    state.record_authenticated_peer("old-active", now - 1000)
    state.record_authenticated_peer("old-active", now)
    state.record_authenticated_peer("new-active", now - 10)
    state.record_authenticated_peer("new-active", now)

    due = state.long_authenticated_peers(100, active_within_seconds=60, now=now)

    assert [item["node_id"] for item in due] == ["old-active"]


def test_server_queues_long_authentication_for_revalidation(tmp_path):
    server = ZDXServer(
        trusted_peers={"peer-a": "placeholder"},
        state_path=str(tmp_path / "state.json"),
        compute_state_path=str(tmp_path / "compute.json"),
        artifact_root=str(tmp_path / "artifacts"),
        rectification_path=str(tmp_path / "rectification.json"),
        reattest_after_seconds=100,
    )
    now = time.time()
    server.state.record_authenticated_peer("peer-a", now - 1000)
    server.state.record_authenticated_peer("peer-a", now)

    server._queue_long_authenticated_peers()
    pending = server.rectification.pending()

    assert len(pending) == 1
    assert pending[0]["target_node_id"] == "peer-a"
    assert pending[0]["reason_code"] == "authentication_age"


def test_verified_replay_pattern_queues_review_without_revoking(tmp_path):
    server = ZDXServer(
        trusted_peers={"peer-a": "placeholder"},
        state_path=str(tmp_path / "state.json"),
        compute_state_path=str(tmp_path / "compute.json"),
        artifact_root=str(tmp_path / "artifacts"),
        rectification_path=str(tmp_path / "rectification.json"),
    )

    server._observe_authenticated_auth_error(
        AuthenticationError(
            stage="replay_protection",
            reason="duplicate signed sequence",
            peer_id="peer-a",
        )
    )

    pending = server.rectification.pending()
    assert len(pending) == 1
    assert pending[0]["reason_code"] == "replay_pattern"
    assert "peer-a" in server._enrolled_peers
    assert "peer-a" not in server._revoked_peers


def test_peer_cannot_fabricate_authentication_age(tmp_path):
    server = ZDXServer(
        trusted_peers={"observer": "placeholder", "peer-a": "placeholder"},
        state_path=str(tmp_path / "state.json"),
        compute_state_path=str(tmp_path / "compute.json"),
        artifact_root=str(tmp_path / "artifacts"),
        rectification_path=str(tmp_path / "rectification.json"),
        reattest_after_seconds=3600,
    )
    now = time.time()
    server.state.record_authenticated_peer("peer-a", now)

    message = ZDXMessage(
        kind="rectification_request",
        peer_id="observer",
        payload={
            "target_node_id": "peer-a",
            "reason_code": "authentication_age",
            "auth_age_seconds": 999999,
        },
    )

    with pytest.raises(PermissionError, match="not due"):
        server._handle_trust(object(), message)

    assert server.rectification.pending_count() == 0


def test_reattest_challenge_is_bound_expiring_and_one_time(tmp_path):
    signer = ZDXEd25519Signer("peer-a", key_path=str(tmp_path / "keys"))
    store = ReattestationChallengeStore(str(tmp_path / "reattest.json"))
    challenge = store.issue(
        request_id="review-1",
        node_id="peer-a",
        ttl_seconds=60,
        now=1000,
    )
    signature = signer.sign_message(store.signed_payload(challenge))

    verified = store.verify(
        challenge_id=challenge["challenge_id"],
        node_id="peer-a",
        signature=signature,
        public_key_pem=signer.get_public_key_pem(),
        now=1030,
    )
    assert verified["status"] == "verified"

    with pytest.raises(ValueError, match="not pending"):
        store.verify(
            challenge_id=challenge["challenge_id"],
            node_id="peer-a",
            signature=signature,
            public_key_pem=signer.get_public_key_pem(),
            now=1031,
        )


def test_reattest_rejects_expired_or_wrong_node(tmp_path):
    signer = ZDXEd25519Signer("peer-a", key_path=str(tmp_path / "keys"))
    store = ReattestationChallengeStore(str(tmp_path / "reattest.json"))
    challenge = store.issue(
        request_id="review-1",
        node_id="peer-a",
        ttl_seconds=10,
        now=1000,
    )
    signature = signer.sign_message(store.signed_payload(challenge))

    with pytest.raises(PermissionError, match="does not belong"):
        store.verify(
            challenge_id=challenge["challenge_id"],
            node_id="peer-b",
            signature=signature,
            public_key_pem=signer.get_public_key_pem(),
            now=1005,
        )

    with pytest.raises(TimeoutError, match="expired"):
        store.verify(
            challenge_id=challenge["challenge_id"],
            node_id="peer-a",
            signature=signature,
            public_key_pem=signer.get_public_key_pem(),
            now=1011,
        )


def test_successful_reattest_clears_only_auth_age_review(tmp_path):
    signer = ZDXEd25519Signer("peer-a", key_path=str(tmp_path / "keys"))
    server = ZDXServer(
        trusted_peers={"peer-a": signer.get_public_key_pem()},
        state_path=str(tmp_path / "state.json"),
        compute_state_path=str(tmp_path / "compute.json"),
        artifact_root=str(tmp_path / "artifacts"),
        rectification_path=str(tmp_path / "rectification.json"),
        reattest_path=str(tmp_path / "reattest.json"),
        reattest_after_seconds=100,
    )
    now = time.time()
    server.state.record_authenticated_peer("peer-a", now - 1000)
    server.state.record_authenticated_peer("peer-a", now)
    server._queue_long_authenticated_peers()
    suspicious = server.rectification.request(
        reporter_node_id="local-auth-monitor",
        target_node_id="peer-a",
        reason_code="suspicious_authenticated_behavior",
        detail="requires separate review",
    )
    challenge = server._reattest_challenge_for_peer("peer-a")
    signature = signer.sign_message({
        key: challenge[key]
        for key in ("domain", "request_id", "node_id", "nonce", "issued_at", "expires_at")
    })
    message = ZDXMessage(
        kind="reattest_response",
        peer_id="peer-a",
        payload={
            "challenge_id": challenge["challenge_id"],
            "challenge_signature": signature,
        },
    )
    sent = []
    server._send = lambda _conn, response: sent.append(response)

    server._handle_reattest_response(object(), message)

    assert sent[-1].kind == "reattest_ack"
    assert sent[-1].payload["accepted"] is True
    assert server.rectification.pending_for_target("peer-a", "authentication_age") == []
    remaining = server.rectification.pending_for_target("peer-a")
    assert [item["request_id"] for item in remaining] == [suspicious["request_id"]]
    assert server.state.long_authenticated_peers(100, now=time.time()) == []
