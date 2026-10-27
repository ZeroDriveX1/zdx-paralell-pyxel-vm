"""Runs only on a machine provisioned with cgroup-v2 rootless Podman."""
import os
import shutil
from pathlib import Path

import pytest

from zdx_compute_core import ComputeTask, sha256_file
from zdx_executor_sandbox import SandboxExecutionError, run_sandboxed_task

pytestmark = pytest.mark.skipif(
    not os.environ.get("ZDX_SANDBOX_IMAGE"),
    reason="immutable local Podman image not provided",
)


def _task(tmp_path, source):
    frame = tmp_path / "frame.png"
    shutil.copyfile(source, frame)
    digest = sha256_file(str(frame))
    return ComputeTask(
        frame_path=str(frame), artifact_digest=digest, frame_sha256=digest,
        memory_mb=256, threads=1, max_seconds=45,
    )


def test_real_sandbox_runs_desktop_png(tmp_path):
    original = Path(__file__).resolve().parents[1] / "android/app/src/androidTest/assets/program.png"
    task = _task(tmp_path, original)
    result = run_sandboxed_task(task)
    assert result["task_id"] == task.task_id
    assert result["shared"]["M0"] == 12


def test_real_sandbox_fails_malformed_png_without_worker_crash(tmp_path):
    source = tmp_path / "source.data"
    source.write_bytes(b"NOT-A-PNG")
    task = _task(tmp_path, source)
    with pytest.raises(SandboxExecutionError):
        run_sandboxed_task(task)
