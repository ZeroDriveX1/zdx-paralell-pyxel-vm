"""Dedicated one-shot VM process. No worker signer, config, or transport imports."""
import json
from pathlib import Path
import sys

sys.path.insert(0, "/app")
from zdx_compute_core import ComputeTask, execute_task, MAX_RESULT_BYTES

MAX_TASK_BYTES = 65536

def main():
    raw = Path("/input/task.json").read_bytes()
    if len(raw) > MAX_TASK_BYTES:
        raise ValueError("task input too large")
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("frame_path") != "/input/frame.png":
        raise ValueError("executor input path mismatch")
    task = ComputeTask.from_dict(data)
    if task.artifact_digest is None or task.frame_sha256 != task.artifact_digest:
        raise ValueError("executor requires verified content-addressed artifact")
    result = execute_task(task)
    body = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(body) > MAX_RESULT_BYTES:
        raise ValueError("executor output too large")
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"{type(exc).__name__}: {str(exc)[:256]}", file=sys.stderr)
        raise SystemExit(2)
