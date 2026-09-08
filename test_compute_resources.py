import json

import pytest

from zdx_compute import ComputeCoordinator, ComputeTask
from zdx_resource_policy import ResourcePolicy, ResourcePolicyStore, ResourceSnapshot


def snapshot(cpu=2.0, available=4096, charging=True):
    return ResourceSnapshot(
        cpu_percent=cpu,
        total_memory_mb=8192,
        available_memory_mb=available,
        cpu_count=4,
        charging=charging,
    )


def test_policy_persists_and_defaults_to_safe_disabled(tmp_path):
    store = ResourcePolicyStore(str(tmp_path / "policy.json"))
    assert store.load().enabled is False
    policy = store.update(enabled=True, memory_limit_mb=1024, idle_only=True)
    assert policy.enabled is True
    assert store.load().memory_limit_mb == 1024


def test_idle_and_memory_admission_guards(tmp_path):
    policy = ResourcePolicy(
        enabled=True,
        idle_only=True,
        idle_cpu_percent=10,
        memory_limit_mb=1024,
        min_free_memory_mb=512,
        production_guard_file=str(tmp_path / "busy"),
    )
    assert policy.admit(snapshot(cpu=5, available=2048), 512)[0]
    assert policy.admit(snapshot(cpu=50, available=2048), 512)[0] is False
    assert policy.admit(snapshot(cpu=5, available=1200), 800)[0] is False
    (tmp_path / "busy").touch()
    assert policy.admit(snapshot(cpu=5, available=2048), 512)[0] is False


def test_coordinator_allocates_only_tasks_within_worker_ram_budget(tmp_path):
    queue = ComputeCoordinator(str(tmp_path / "compute.json"))
    queue.register_worker("worker", {"memory_mb": 2048})
    small = queue.submit(ComputeTask("small.png", "a" * 64, memory_mb=512))
    large = queue.submit(ComputeTask("large.png", "b" * 64, memory_mb=2048))

    claimed = queue.claim("worker", available_memory_mb=1024, cpu_count=1)
    assert claimed.task_id == small.task_id
    queue.release("worker", claimed.task_id, "test")
    assert queue.claim("worker", available_memory_mb=1024, cpu_count=1).task_id == small.task_id
    assert large.task_id in queue.status()["queued"]


def test_compute_state_survives_reload(tmp_path):
    path = tmp_path / "compute.json"
    queue = ComputeCoordinator(str(path))
    queue.register_worker("worker", {})
    task = queue.submit(ComputeTask("frame.png", "c" * 64))
    assert ComputeCoordinator(str(path)).status()["queued"][task.task_id]
