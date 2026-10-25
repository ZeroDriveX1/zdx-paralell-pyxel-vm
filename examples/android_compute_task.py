"""Create a real mobile-compatible program and its artifact-backed task JSON.

Run from the repository: python -m examples.android_compute_task
Upload android-program.png through your existing artifact ingestion path before
submitting android-task.json to the coordinator. No network writes happen here.
"""

import json
from pathlib import Path

from zdx_android_vm import ADAPTER_ID, FEATURE
from zdx_compute import ComputeTask, sha256_file
from zdx_parallel_vm import SimpleCompiler


def main():
    frame = Path("android-program.png")
    SimpleCompiler().compile([
        ["SET_A 5", "SET_B 7", "ADD", "COPY_OUT", "STORE_MEM 0", "HALT"],
    ], str(frame))
    task = ComputeTask(
        artifact_digest=sha256_file(str(frame)), memory_mb=128, threads=1, max_seconds=30,
        metadata={
            "android_vm_adapter_id": ADAPTER_ID,
            "artifact_type": "pyxel-frame-v1", "execution_model": "pixel-frame",
            "required_vm_features": [FEATURE],
            "required_android_capabilities": [FEATURE, "isolated-process"],
        },
    ).validate()
    Path("android-task.json").write_text(json.dumps(task.to_dict(), indent=2) + "\n", encoding="utf-8")
    print("Created android-program.png and android-task.json; expected result: shared.M0 = 12")


if __name__ == "__main__":
    main()
