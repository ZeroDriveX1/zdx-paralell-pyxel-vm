import json
import time

import pytest

from zdx_compute_core import ComputeCoordinator, ComputeTask


def make_task(path, task_id, max_seconds=300):
    return ComputeTask(frame_path=str(path), task_id=task_id, max_seconds=max_seconds, memory_mb=1)


def make_queue(tmp_path, **kwargs):
    return ComputeCoordinator(state_path=str(tmp_path / "state.json"), **kwargs)


def test_claim_complete_requires_matching_lease_id(tmp_path):
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"x")
    queue = make_queue(tmp_path)
    queue.register_worker("w1", {"cpu_count": 4})
    queue.submit(make_task(frame, "t1"))
    task = queue.claim("w1", 4096, 4)
    lease_id = task.metadata["lease_id"]
    queue.complete("w1", "t1", {"r": 1}, lease_id=lease_id)
    assert queue.status()["completed"]["t1"]["result"] == {"r": 1}


def test_stale_lease_is_rejected_after_reclaim(tmp_path):
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"x")
    queue = make_queue(tmp_path, lease_seconds=1)
    queue.register_worker("w1", {"cpu_count": 4})
    queue.submit(make_task(frame, "t2", 1))
    old_lease = queue.claim("w1", 4096, 4).metadata["lease_id"]
    time.sleep(1.2)
    new_lease = queue.claim("w1", 4096, 4).metadata["lease_id"]
    assert old_lease != new_lease
    try:
        queue.complete("w1", "t2", {"stale": True}, lease_id=old_lease)
    except ValueError:
        pass
    else:
        raise AssertionError("stale lease was accepted")
    queue.complete("w1", "t2", {"fresh": True}, lease_id=new_lease)


def test_release_retires_poison_task_at_attempt_cap(tmp_path):
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"x")
    queue = make_queue(tmp_path, max_attempts=3)
    queue.register_worker("w1", {"cpu_count": 4})
    queue.submit(make_task(frame, "t3"))
    for _ in range(10):
        task = queue.claim("w1", 4096, 4)
        if task is None:
            break
        queue.release("w1", "t3", "worker crashed", lease_id=task.metadata["lease_id"])
    state = queue.status()
    assert state["failed"]["t3"]["attempts"] == 3
    assert "t3" not in state["queued"]


def test_expired_lease_retires_at_attempt_cap(tmp_path):
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"x")
    queue = make_queue(tmp_path, lease_seconds=1, max_attempts=2)
    queue.register_worker("w1", {"cpu_count": 4})
    queue.submit(make_task(frame, "t4", 1))
    for _ in range(5):
        if queue.claim("w1", 4096, 4) is None:
            break
        time.sleep(1.2)
    assert "t4" in queue.status()["failed"]


def test_legacy_running_state_without_lease_id_still_completes(tmp_path):
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"x")
    state_path = tmp_path / "old.json"
    with state_path.open("w", encoding="utf-8") as stream:
        json.dump({"version": 1, "queued": {}, "running": {"t5": {
            "task": make_task(frame, "t5").to_dict(), "worker_id": "w1",
            "leased_at": time.time(), "lease_until": time.time() + 600}},
            "completed": {}, "failed": {}, "workers": {}}, stream)
    queue = ComputeCoordinator(state_path=str(state_path))
    queue.complete("w1", "t5", {"legacy": True})
    assert "t5" in queue.status()["completed"]


def test_wrong_worker_is_rejected_even_with_lease_id(tmp_path):
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"x")
    queue = make_queue(tmp_path)
    queue.register_worker("w1", {"cpu_count": 4})
    queue.register_worker("w2", {"cpu_count": 4})
    queue.submit(make_task(frame, "t6"))
    task = queue.claim("w1", 4096, 4)
    try:
        queue.complete("w2", "t6", {}, lease_id=task.metadata["lease_id"])
    except ValueError:
        pass
    else:
        raise AssertionError("wrong worker was accepted")


@pytest.mark.parametrize("operation", ["complete", "release", "fail"])
def test_current_lease_cannot_be_finalized_without_token(tmp_path, operation):
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"x")
    queue = make_queue(tmp_path)
    queue.register_worker("w1", {"cpu_count": 4})
    queue.submit(make_task(frame, "t7"))
    lease = queue.claim("w1", 4096, 4)
    assert lease.metadata["lease_id"]

    if operation == "complete":
        call = lambda: queue.complete("w1", "t7", {})
    elif operation == "release":
        call = lambda: queue.release("w1", "t7", "no lease")
    else:
        call = lambda: queue.fail("w1", "t7", "no lease")

    with pytest.raises(ValueError, match="stale or invalid lease"):
        call()
    assert "t7" in queue.status()["running"]

@pytest.mark.parametrize("operation", ["complete", "release", "fail"])
def test_expired_lease_rejected_without_status_or_claim_sweep(tmp_path, monkeypatch, operation):
    import zdx_compute_core as core

    frame = tmp_path / "frame.png"
    frame.write_bytes(b"x")
    clock = [1000.0]
    monkeypatch.setattr(core.time, "time", lambda: clock[0])
    queue = make_queue(tmp_path, lease_seconds=20, max_attempts=2)
    queue.register_worker("w1", {"cpu_count": 1})
    queue.submit(make_task(frame, "expired", 200))
    old = queue.claim("w1", 4096).metadata["lease_id"]
    clock[0] = 1020.0  # Exact deadline: no intervening queue/status sweep.

    if operation == "complete":
        call = lambda: queue.complete("w1", "expired", {"late": True}, lease_id=old)
    elif operation == "release":
        call = lambda: queue.release("w1", "expired", "late", lease_id=old)
    else:
        call = lambda: queue.fail("w1", "expired", "late", lease_id=old)

    with pytest.raises(ValueError, match="not leased"):
        call()
    state = queue.status()
    assert "expired" in state["queued"]
    assert "expired" not in state["running"]
    assert "expired" not in state["completed"]
    assert "expired" not in state["failed"]
    assert state["attempts"]["expired"] == 1
    assert ComputeCoordinator(str(tmp_path / "state.json")).status()["queued"]["expired"]


def test_expired_lease_attempt_cap_is_durable(tmp_path, monkeypatch):
    import zdx_compute_core as core

    clock = [2000.0]
    monkeypatch.setattr(core.time, "time", lambda: clock[0])
    frame = tmp_path / "f.png"
    frame.write_bytes(b"x")
    queue = make_queue(tmp_path, lease_seconds=5, max_attempts=1)
    queue.register_worker("w1", {"cpu_count": 1})
    queue.submit(make_task(frame, "terminal", 100))
    token = queue.claim("w1", 4096).metadata["lease_id"]
    clock[0] += 5
    with pytest.raises(ValueError, match="not leased"):
        queue.complete("w1", "terminal", {}, lease_id=token)
    assert queue.status()["failed"]["terminal"]["error"] == "lease expired"
    assert "terminal" not in queue.status()["queued"]
