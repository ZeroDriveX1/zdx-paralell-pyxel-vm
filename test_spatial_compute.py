import os

from zdx_compute import ComputeCoordinator, ComputeTask, execute_task, sha256_file
from zdx_spatial_frame import SpatialCompiler, SpatialLayout


def test_spatial_compute_task_executes_declared_geometry(tmp_path):
    layout = SpatialLayout(width=32, height=16, execution_rows=1)
    path = str(tmp_path / "spatial-task.png")
    SpatialCompiler(layout).compile(
        [["SET_A 6", "SET_B 7", "ADD", "COPY_OUT", "STORE_MEM 0", "HALT"]],
        path,
    )
    task = ComputeTask(
        frame_path=path,
        frame_sha256=sha256_file(path),
        threads=1,
        metadata={
            "execution_model": "spatial-png",
            "spatial_layout": layout.to_dict(),
            "required_vm_features": ["spatial-png-v1"],
        },
    )
    result = execute_task(task)
    assert result["execution_model"] == "spatial-png"
    assert result["registers"]["T0"]["OUT"] == 13
    assert result["shared"]["M0"] == 13


def test_spatial_task_is_not_leased_to_incapable_worker(tmp_path):
    layout = SpatialLayout(width=16, height=8, execution_rows=1)
    queue = ComputeCoordinator(str(tmp_path / "compute.json"))
    queue.register_worker("legacy", {"vm_features": ["pyxel-vm"]})
    queue.register_worker(
        "spatial",
        {"vm_features": ["pyxel-vm", "spatial-png-v1", "xy-addressing", "rgb24-isa16", "spatial-storage"]},
    )
    task = queue.submit(
        ComputeTask(
            frame_path="frame.png",
            frame_sha256="a" * 64,
            metadata={
                "execution_model": "spatial-png",
                "spatial_layout": layout.to_dict(),
            },
        )
    )
    assert queue.claim("legacy", available_memory_mb=1024, cpu_count=1) is None
    claimed = queue.claim("spatial", available_memory_mb=1024, cpu_count=1)
    assert claimed is not None
    assert claimed.task_id == task.task_id
    assert "spatial-png-v1" in claimed.metadata["required_vm_features"]
