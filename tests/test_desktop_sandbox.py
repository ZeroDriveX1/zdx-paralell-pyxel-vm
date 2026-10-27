import re
from pathlib import Path

import pytest

from zdx_executor_sandbox import (
    SandboxUnavailable, _image_ref, build_sandbox_command, sandbox_ready,
)

IMAGE = "sha256:" + "a" * 64


def test_requires_immutable_digest():
    for bad in ("", "latest", "python:3.12", "sha256:xyz",
                "registry.test/foo:latest", "-v /:/host"):
        with pytest.raises(SandboxUnavailable):
            _image_ref(bad)
    assert _image_ref(IMAGE) == IMAGE
    assert _image_ref("registry.example/pyxel@sha256:" + "f" * 64).endswith("f" * 64)


def test_executor_command_has_no_network_or_ambient_host_access(tmp_path):
    frame = tmp_path / "frame.png"
    metadata = tmp_path / "task.json"
    frame.write_bytes(b"PNG")
    metadata.write_bytes(b"{}")
    name = "zdx-job-" + "1" * 32
    args = build_sandbox_command(
        image=IMAGE, frame=frame.resolve(), task_json=metadata.resolve(),
        name=name, memory_mb=256, threads=2, max_seconds=15,
    )
    text = " ".join(args)
    assert args[0:2] == ["podman", "run"]
    for token in ("--network=none", "--read-only", "--cap-drop=ALL",
                  "--security-opt=no-new-privileges", "--userns=keep-id",
                  "--pids-limit=64", "--memory=256m", "--memory-swap=257m",
                  "--cpus=2", "--pull=never", "--unsetenv-all"):
        assert token in args
    assert text.count("type=bind,") == 2
    assert "dst=/input/frame.png,ro" in text
    assert "dst=/input/task.json,ro" in text
    assert "/var/lib/zdx-worker/keys" not in text
    assert args[-1] == IMAGE


@pytest.mark.parametrize("memory,threads,seconds", [(0, 1, 1), (32769, 1, 1), (1, 0, 1), (1, 1, 0)])
def test_invalid_sandbox_limits_fail_closed(tmp_path, memory, threads, seconds):
    with pytest.raises(ValueError):
        build_sandbox_command(
            image=IMAGE, frame=(tmp_path / "a").resolve(), task_json=(tmp_path / "b").resolve(),
            name="zdx-job-" + "2" * 32, memory_mb=memory, threads=threads, max_seconds=seconds,
        )


def test_missing_image_is_fail_closed(monkeypatch):
    import zdx_executor_sandbox as sandbox
    monkeypatch.setattr(sandbox.sys, "platform", "linux")
    monkeypatch.setattr(sandbox.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(sandbox.shutil, "which", lambda cmd: None)
    ok, reason = sandbox_ready(IMAGE)
    assert not ok and "podman" in reason


def test_invalid_image_cannot_claim_even_when_podman_installed(monkeypatch):
    import zdx_executor_sandbox as sandbox
    monkeypatch.setattr(sandbox.sys, "platform", "linux")
    monkeypatch.setattr(sandbox.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(sandbox.shutil, "which", lambda cmd: "/usr/bin/podman")
    ok, reason = sandbox_ready("mutable:latest")
    assert not ok and "immutable" in reason
