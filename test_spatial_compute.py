import os

import pytest

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


def test_spatial_compute_rejects_thread_geometry_mismatch(tmp_path):
    layout = SpatialLayout(width=16, height=8, execution_rows=2)
    path = str(tmp_path / "mismatch.png")
    SpatialCompiler(layout).compile([["HALT"], ["HALT"]], path)
    task = ComputeTask(
        frame_path=path,
        frame_sha256=sha256_file(path),
        threads=1,
        metadata={
            "execution_model": "spatial-png",
            "spatial_layout": layout.to_dict(),
        },
    )
    with pytest.raises(ValueError, match="threads must equal"):
        task.validate()


def test_spatial_compute_rejects_unknown_spatial_version(tmp_path):
    layout = SpatialLayout(width=16, height=8, execution_rows=1)
    task = ComputeTask(
        frame_path="frame.png",
        frame_sha256="a" * 64,
        threads=1,
        metadata={
            "execution_model": "spatial-png",
            "spatial_version": 2,
            "spatial_layout": layout.to_dict(),
        },
    )
    with pytest.raises(ValueError, match="unsupported spatial_version"):
        task.validate()


def test_spatial_compute_memory_admission_accounts_for_raster_working_set():
    layout = SpatialLayout(width=4096, height=4096, execution_rows=1)
    task = ComputeTask(
        frame_path="frame.png",
        frame_sha256="a" * 64,
        threads=1,
        memory_mb=128,
        metadata={
            "execution_model": "spatial-png",
            "spatial_layout": layout.to_dict(),
        },
    )
    with pytest.raises(ValueError, match="working-set estimate"):
        task.validate()


def test_spatial_compute_canonicalizes_layout_metadata():
    task = ComputeTask(
        frame_path="frame.png",
        frame_sha256="a" * 64,
        threads=1,
        metadata={
            "execution_model": "spatial-png",
            "spatial_layout": {
                "width": 16,
                "height": 8,
                "execution_rows": 1,
            },
        },
    ).validate()
    assert task.metadata["spatial_version"] == 1
    assert task.metadata["spatial_layout"]["raw_capacity_bytes"] == 16 * 8 * 3
    assert "spatial-png-v1" in task.metadata["required_vm_features"]
