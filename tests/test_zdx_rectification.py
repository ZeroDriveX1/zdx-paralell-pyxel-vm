import time

import pytest

from zdx_auth_pipeline import AuthenticationError
from zdx_network import ZDXMessage
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
