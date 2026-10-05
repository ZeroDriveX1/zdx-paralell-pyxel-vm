"""Cross-subsystem release validation for auth, re-auth, compute, and RAM."""

import threading
import time

import pytest

from zdx_compute_core import ComputeCoordinator, ComputeTask
from zdx_ed25519_signer import ZDXEd25519Signer
from zdx_network import ZDXMessage
from zdx_node import ZDXNode
from zdx_resource_policy import ResourcePolicy, ResourceSnapshot
from zdx_server_core import ZDXServer


def _wait_for_port(server, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if server.port:
            return
        time.sleep(0.01)
    raise AssertionError("server did not bind")


def test_network_auth_heartbeat_reattest_roundtrip(tmp_path):
    signer = ZDXEd25519Signer("worker-a", key_path=str(tmp_path / "keys"))
    server = ZDXServer(
        host="127.0.0.1",
        port=0,
        require_auth=True,
        trusted_peers={"worker-a": signer.get_public_key_pem()},
        state_path=str(tmp_path / "state.json"),
        compute_state_path=str(tmp_path / "compute.json"),
        artifact_root=str(tmp_path / "artifacts"),
        rectification_path=str(tmp_path / "rectification.json"),
        reattest_path=str(tmp_path / "reattest.json"),
        reattest_after_seconds=1,
    )

    old = time.time() - 60
    server.state.record_authenticated_peer("worker-a", old)
    thread = threading.Thread(target=server.serve, daemon=True)
    thread.start()
    _wait_for_port(server)

    node = ZDXNode(
        host="127.0.0.1",
        port=server.port,
        node_id="worker-a",
        signer=signer,
    )
    try:
        sock = node.connect()
        try:
            heartbeat = node.ping(sock)
            assert heartbeat.kind == "heartbeat"
            challenge = heartbeat.payload["reattest_challenge"]
            assert challenge["node_id"] == "worker-a"

            signed_payload = {
                key: challenge[key]
                for key in (
                    "domain", "request_id", "node_id", "nonce",
                    "issued_at_ms", "expires_at_ms",
                )
            }
            response = node.request(
                sock,
                ZDXMessage(
                    kind="reattest_response",
                    payload={
                        "challenge_id": challenge["challenge_id"],
                        "challenge_signature": signer.sign_message(signed_payload),
                    },
                ),
            )
            assert response.kind == "reattest_ack"
            assert response.payload["accepted"] is True
            assert server.rectification.pending_for_target(
                "worker-a", "authentication_age"
            ) == []

            follow_up = node.ping(sock)
            assert follow_up.payload["reattest_challenge"] is None
        finally:
            sock.close()
    finally:
        server.stop()
        thread.join(timeout=2)


def test_invalid_reattest_proof_does_not_clear_review(tmp_path):
    signer = ZDXEd25519Signer("worker-a", key_path=str(tmp_path / "a"))
    attacker = ZDXEd25519Signer("attacker", key_path=str(tmp_path / "b"))
    server = ZDXServer(
        trusted_peers={"worker-a": signer.get_public_key_pem()},
        state_path=str(tmp_path / "state.json"),
        compute_state_path=str(tmp_path / "compute.json"),
        artifact_root=str(tmp_path / "artifacts"),
        rectification_path=str(tmp_path / "rectification.json"),
        reattest_path=str(tmp_path / "reattest.json"),
        reattest_after_seconds=1,
    )
    now = time.time()
    server.state.record_authenticated_peer("worker-a", now - 60)
    server.state.record_authenticated_peer("worker-a", now)
    server._queue_long_authenticated_peers()
    challenge = server._reattest_challenge_for_peer("worker-a")
    signed_payload = {
        key: challenge[key]
        for key in (
            "domain", "request_id", "node_id", "nonce",
            "issued_at_ms", "expires_at_ms",
        )
    }
    message = ZDXMessage(
        kind="reattest_response",
        peer_id="worker-a",
        payload={
            "challenge_id": challenge["challenge_id"],
            "challenge_signature": attacker.sign_message(signed_payload),
        },
    )

    with pytest.raises(PermissionError, match="invalid re-attestation signature"):
        server._handle_reattest_response(object(), message)

    assert len(server.rectification.pending_for_target(
        "worker-a", "authentication_age"
    )) == 1


def test_ram_and_threads_are_reserved_across_concurrent_leases(tmp_path):
    queue = ComputeCoordinator(str(tmp_path / "compute.json"))
    queue.register_worker(
        "worker-a",
        {
            "memory_mb": 4096,
            "cpu_count": 4,
            "safe_limits": {"memory_limit_mb": 2048},
        },
    )
    queue.submit(ComputeTask(
        frame_path="a.png", frame_sha256="a" * 64,
        task_id="a", memory_mb=1536, threads=2,
    ))
    queue.submit(ComputeTask(
        frame_path="b.png", frame_sha256="b" * 64,
        task_id="b", memory_mb=1024, threads=2,
    ))
    queue.submit(ComputeTask(
        frame_path="c.png", frame_sha256="c" * 64,
        task_id="c", memory_mb=512, threads=1,
    ))

    first = queue.claim("worker-a", available_memory_mb=4096, cpu_count=8)
    assert first.task_id == "a"

    # 1536 MB of the 2048 MB safe cap and 2/4 threads are already reserved.
    second = queue.claim("worker-a", available_memory_mb=4096, cpu_count=8)
    assert second.task_id == "c"

    # Remaining memory is zero and one thread remains; b cannot overcommit.
    assert queue.claim("worker-a", available_memory_mb=4096, cpu_count=8) is None

    queue.complete("worker-a", "a", {"ok": True}, lease_id=first.metadata["lease_id"])
    third = queue.claim("worker-a", available_memory_mb=4096, cpu_count=8)
    assert third.task_id == "b"


def test_resource_policy_budget_matches_coordinator_claim_contract(tmp_path):
    policy = ResourcePolicy(
        enabled=True,
        idle_only=False,
        memory_limit_mb=2048,
        min_free_memory_mb=1024,
        max_concurrent_tasks=2,
        production_guard_file=str(tmp_path / "not-busy"),
    )
    snapshot = ResourceSnapshot(
        cpu_percent=5,
        total_memory_mb=8192,
        available_memory_mb=2560,
        cpu_count=4,
        charging=True,
    )
    budget = policy.available_budget_mb(snapshot)
    assert budget == 1536
    assert policy.admit(snapshot, 1536)[0] is True
    assert policy.admit(snapshot, 1537)[0] is False

    queue = ComputeCoordinator(str(tmp_path / "compute.json"))
    queue.register_worker("worker", {
        "memory_mb": snapshot.total_memory_mb,
        "cpu_count": snapshot.cpu_count,
        "safe_limits": {"memory_limit_mb": policy.memory_limit_mb},
    })
    queue.submit(ComputeTask(
        frame_path="fits.png", frame_sha256="d" * 64,
        task_id="fits", memory_mb=1536, threads=1,
    ))
    queue.submit(ComputeTask(
        frame_path="too-big.png", frame_sha256="e" * 64,
        task_id="too-big", memory_mb=1537, threads=1,
    ))

    assert queue.claim("worker", budget, snapshot.cpu_count).task_id == "fits"
    assert queue.claim("worker", budget, snapshot.cpu_count) is None


def test_distributed_claims_isolate_worker_ram_and_survive_restart(tmp_path):
    path = tmp_path / "compute.json"
    queue = ComputeCoordinator(str(path))
    queue.register_worker("small", {"memory_mb": 1024, "cpu_count": 2})
    queue.register_worker("large", {"memory_mb": 8192, "cpu_count": 8})

    queue.submit(ComputeTask(
        frame_path="small.png", frame_sha256="1" * 64,
        task_id="small-task", memory_mb=512, threads=1,
    ))
    queue.submit(ComputeTask(
        frame_path="large.png", frame_sha256="2" * 64,
        task_id="large-task", memory_mb=4096, threads=4,
    ))

    small = queue.claim("small", available_memory_mb=1024, cpu_count=2)
    assert small.task_id == "small-task"
    assert queue.claim("small", available_memory_mb=1024, cpu_count=2) is None

    large = queue.claim("large", available_memory_mb=8192, cpu_count=8)
    assert large.task_id == "large-task"

    restarted = ComputeCoordinator(str(path))
    state = restarted.status()
    assert state["running"]["small-task"]["worker_id"] == "small"
    assert state["running"]["large-task"]["worker_id"] == "large"

    restarted.complete(
        "small", "small-task", {"worker": "small"},
        lease_id=small.metadata["lease_id"],
    )
    restarted.complete(
        "large", "large-task", {"worker": "large"},
        lease_id=large.metadata["lease_id"],
    )
    final = restarted.status()
    assert set(final["completed"]) == {"small-task", "large-task"}
    assert final["running"] == {}
