"""Fail-closed, rootless OCI isolation for untrusted desktop Pyxel artifacts.

The long-lived network worker never imports or executes a remote VM program.
Only content-addressed frames and bounded, unprivileged task metadata enter
a short-lived container. Rootless Podman + cgroup v2 are required.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid

from zdx_compute_core import ComputeTask, MAX_RESULT_BYTES, sha256_file

MAX_INPUT_BYTES = 65536
MAX_FRAME_BYTES = 8 * 1024 * 1024
MAX_STDERR_BYTES = 8192
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
_IMAGE_REF = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._/:\-]*@sha256:[0-9a-f]{64}\Z")


class SandboxUnavailable(RuntimeError):
    """Executor unavailable: no workload may run in the coordinator process."""


class SandboxExecutionError(RuntimeError):
    """The disposable executor failed or exceeded a hard resource limit."""


def _image_ref(image: str) -> str:
    if not isinstance(image, str) or not (_IMAGE_ID.fullmatch(image) or _IMAGE_REF.fullmatch(image)):
        raise SandboxUnavailable("ZDX_SANDBOX_IMAGE must be an immutable image ID or name@sha256 digest")
    return image


def _podman_environment() -> dict[str, str]:
    # These variables are needed by rootless Podman, NOT forwarded to the VM.
    allowed = ("HOME", "XDG_RUNTIME_DIR", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "PATH")
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env.setdefault("PATH", os.defpath)
    return env


def sandbox_ready(image: str | None = None) -> tuple[bool, str]:
    if sys.platform != "linux" or not hasattr(os, "geteuid") or os.geteuid() == 0:
        return False, "rootless Linux Podman sandbox required"
    try:
        image = _image_ref(image or os.environ.get("ZDX_SANDBOX_IMAGE", ""))
    except SandboxUnavailable as exc:
        return False, str(exc)
    executable = shutil.which("podman")
    if not executable:
        return False, "podman unavailable"
    try:
        check = subprocess.run(
            [executable, "image", "exists", image], env=_podman_environment(),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"rootless image inspection failed: {type(exc).__name__}"
    if check.returncode:
        return False, "immutable sandbox image not available locally"
    return True, ""


def build_sandbox_command(
    *, image: str, frame: Path, task_json: Path, name: str,
    memory_mb: int, threads: int, max_seconds: int,
) -> list[str]:
    image = _image_ref(image)
    if not (1 <= memory_mb <= 32768 and 1 <= threads <= 256 and 1 <= max_seconds <= 86400):
        raise ValueError("unbounded executor resource request")
    if not re.fullmatch(r"zdx-job-[0-9a-f]{32}", name):
        raise ValueError("invalid sandbox name")
    if not frame.is_absolute() or not task_json.is_absolute():
        raise ValueError("sandbox inputs must be absolute paths")
    # cgroup memory is a hard per-task limit; swap must not double the lease.
    return [
        "podman", "run", "--rm", "--pull=never", "--name", name,
        "--network=none", "--pid=private", "--ipc=private", "--read-only",
        "--userns=keep-id", "--user", f"{os.getuid()}:{os.getgid()}",
        "--cap-drop=ALL", "--security-opt=no-new-privileges",
        "--pids-limit=64", f"--memory={memory_mb}m",
        f"--memory-swap={memory_mb + 1}m", f"--cpus={min(threads, 8)}",
        "--ulimit=nofile=64:64", f"--ulimit=cpu={max_seconds + 2}:{max_seconds + 2}",
        "--tmpfs=/tmp:rw,nosuid,nodev,size=16m",
        "--unsetenv-all", "--env=HOME=/tmp", "--env=PYTHONNOUSERSITE=1",
        "--env=PYTHONDONTWRITEBYTECODE=1", "--env=OPENBLAS_NUM_THREADS=1",
        "--env=OMP_NUM_THREADS=1",
        "--mount", f"type=bind,src={frame},dst=/input/frame.png,ro",
        "--mount", f"type=bind,src={task_json},dst=/input/task.json,ro",
        image,
    ]


def run_sandboxed_task(task: ComputeTask, *, stop_event=None, image: str | None = None) -> dict:
    task.validate()
    image = _image_ref(image or os.environ.get("ZDX_SANDBOX_IMAGE", ""))
    if sys.platform != "linux" or os.geteuid() == 0 or not shutil.which("podman"):
        raise SandboxUnavailable("rootless Linux Podman sandbox required")
    if not task.artifact_digest:
        raise SandboxExecutionError("untrusted workloads require a content-addressed artifact")
    source = Path(task.frame_path)
    if source.is_symlink() or not source.is_file() or not stat.S_ISREG(source.stat().st_mode):
        raise SandboxExecutionError("artifact is not a regular file")
    if not 0 < source.stat().st_size <= MAX_FRAME_BYTES:
        raise SandboxExecutionError("artifact exceeds bounded input limit")
    if sha256_file(str(source)) != task.artifact_digest:
        raise SandboxExecutionError("artifact hash mismatch before isolation")

    data = task.to_dict()
    data["frame_path"] = "/input/frame.png"
    task_bytes = json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(task_bytes) > MAX_INPUT_BYTES:
        raise SandboxExecutionError("serialized task exceeds admission bound")
    name = f"zdx-job-{uuid.uuid4().hex}"
    executable = shutil.which("podman")
    environment = _podman_environment()
    with tempfile.TemporaryDirectory(prefix="zdx-sandbox-") as temporary:
        task_json = Path(temporary) / "task.json"
        task_json.write_bytes(task_bytes)
        task_json.chmod(0o600)
        command = build_sandbox_command(
            image=image, frame=source.resolve(), task_json=task_json.resolve(),
            name=name, memory_mb=task.memory_mb, threads=task.threads,
            max_seconds=task.max_seconds,
        )
        command[0] = executable
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            proc = None
            try:
                proc = subprocess.Popen(
                    command, cwd="/", env=environment, stdin=subprocess.DEVNULL,
                    stdout=stdout, stderr=stderr, close_fds=True, start_new_session=True,
                )
                deadline = time.monotonic() + task.max_seconds
                while proc.poll() is None:
                    if stop_event is not None and stop_event.is_set():
                        raise SandboxExecutionError("executor cancelled by worker shutdown")
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"executor exceeded {task.max_seconds} seconds")
                    if os.fstat(stdout.fileno()).st_size > MAX_RESULT_BYTES or os.fstat(stderr.fileno()).st_size > MAX_STDERR_BYTES:
                        raise SandboxExecutionError("executor output limit exceeded")
                    time.sleep(0.05)
                if os.fstat(stdout.fileno()).st_size > MAX_RESULT_BYTES:
                    raise SandboxExecutionError("executor result exceeds maximum size")
                if proc.returncode:
                    stderr.seek(0)
                    summary = stderr.read(512).decode("utf-8", "replace").replace("\n", " ")
                    raise SandboxExecutionError(f"executor exited {proc.returncode}: {summary}")
                stdout.seek(0)
                result = json.loads(stdout.read(MAX_RESULT_BYTES + 1))
                if not isinstance(result, dict) or result.get("task_id") != task.task_id:
                    raise SandboxExecutionError("executor result identity mismatch")
                return result
            finally:
                if proc is not None and proc.poll() is None:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pass
                # Killing the CLI alone may leave an OCI child running.
                # Remove by the unpredictable generated name in every path.
                try:
                    subprocess.run(
                        [executable, "rm", "--force", "--ignore", name],
                        env=environment, stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=10, check=False,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    pass
