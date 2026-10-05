"""Reproducible microbenchmarks for spatial PNG execution and agent-state paths.

These measurements intentionally exclude model/provider inference. They isolate
VM decode/execution and local persistence costs so changes to the spatial hot
path can be compared without network or LLM variance.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import tempfile
import time
from pathlib import Path

from pyxel_registry import PyxelRegistry
from zdx_agent_runtime import ZDXAgentRuntime
from zdx_pixel_memory import ZDXAgentMemory
from zdx_spatial_frame import SpatialCompiler, SpatialFrame, SpatialLayout, SpatialPyxelVM, SpatialRegion


def summarize(name: str, samples: list[float]) -> dict:
    ordered = sorted(samples)
    p95_index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * 0.95) - 1))
    total = sum(samples)
    return {
        "name": name,
        "iterations": len(samples),
        "total_seconds": total,
        "mean_seconds": statistics.fmean(samples),
        "p50_seconds": statistics.median(samples),
        "p95_seconds": ordered[p95_index],
        "min_seconds": ordered[0],
        "max_seconds": ordered[-1],
        "operations_per_second": len(samples) / total if total else float("inf"),
    }


def timed(iterations: int, fn) -> list[float]:
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - started)
    return samples


def payload_for(iteration: int) -> dict:
    return {
        "shared_state": {f"M{i}": (iteration + i) & 0xFF for i in range(8)},
        "register_state": {
            "T0": {"A": 17, "B": 5, "OUT": 17, "T": 5},
        },
        "spatial_layout": {
            "width": 64,
            "height": 32,
            "execution_rows": 1,
        },
        "source_frame": "/benchmark/frame.png",
    }


def run_benchmarks(iterations: int = 50) -> dict:
    if iterations < 5:
        raise ValueError("iterations must be >= 5")

    with tempfile.TemporaryDirectory(prefix="zdx-spatial-bench-") as tmp:
        root = Path(tmp)
        frame_path = root / "agent-frame.png"
        layout = SpatialLayout(
            width=64,
            height=32,
            execution_rows=1,
            regions=(SpatialRegion("memory", 0, 1, 64, 31),),
        )
        program = [[
            "SET_A 12",
            "SET_B 5",
            "ADD",
            "COPY_OUT",
            "STORE_MEM 0",
            "HALT",
        ]]
        SpatialCompiler(layout).compile(program, str(frame_path))
        resident_frame = SpatialFrame.open(str(frame_path), layout)

        def resident_vm():
            vm = SpatialPyxelVM(layout=layout, strict_mode=True, persist_shared=False)
            vm.execute_spatial_frame(resident_frame)
            if vm.shared["M0"] != 17:
                raise AssertionError("resident VM produced wrong result")

        def file_vm():
            vm = SpatialPyxelVM(layout=layout, strict_mode=True, persist_shared=False)
            vm.execute_spatial(str(frame_path))
            if vm.shared["M0"] != 17:
                raise AssertionError("file VM produced wrong result")

        # Warm both execution paths before timing.
        resident_vm()
        file_vm()

        resident_samples = timed(iterations, resident_vm)
        file_samples = timed(iterations, file_vm)

        same_frame_path = root / "same-frame-agent.png"
        SpatialCompiler(layout).compile(program, str(same_frame_path))
        registry = PyxelRegistry()
        registry.register(
            "vm",
            SpatialPyxelVM(layout=layout, strict_mode=True, persist_shared=False),
        )
        registry.register(
            "memory",
            ZDXAgentMemory(
                agent_id="resident-agent",
                spatial=True,
                spatial_path=str(same_frame_path),
                spatial_layout=layout,
                spatial_region="memory",
            ),
        )
        runtime = ZDXAgentRuntime(registry, checkpoint_interval=10)

        def same_frame_agent_checkpoint():
            result = runtime.run_spatial(str(same_frame_path))
            if result["T0"]["OUT"] != 17:
                raise AssertionError("same-frame agent produced wrong result")
            runtime.checkpoint(str(same_frame_path), barrier=True)

        same_frame_agent_checkpoint()
        same_frame_samples = timed(iterations, same_frame_agent_checkpoint)
        runtime.close()

        compatibility = ZDXAgentMemory(
            agent_id="compat-agent",
            base_dir=str(root / "compat-memory"),
            spatial=False,
        )
        compat_counter = 0

        def compatibility_persist():
            nonlocal compat_counter
            compatibility.update(payload_for(compat_counter))
            compat_counter += 1

        compatibility_persist()
        compatibility_samples = timed(iterations, compatibility_persist)

        json_counter = 0

        def json_roundtrip():
            nonlocal json_counter
            encoded = json.dumps(
                payload_for(json_counter),
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            decoded = json.loads(encoded)
            if decoded["shared_state"]["M0"] != (json_counter & 0xFF):
                raise AssertionError("JSON roundtrip mismatch")
            json_counter += 1

        json_roundtrip_samples = timed(iterations * 10, json_roundtrip)

        resident = summarize("resident_spatial_vm", resident_samples)
        file_based = summarize("file_decoded_spatial_vm", file_samples)
        same_frame = summarize("same_frame_agent_barrier_checkpoint", same_frame_samples)
        compatibility_result = summarize(
            "compatibility_agent_memory_update", compatibility_samples
        )
        json_result = summarize("canonical_json_roundtrip", json_roundtrip_samples)

        return {
            "schema_version": 1,
            "scope": "local deterministic execution/persistence only; no LLM/provider inference",
            "layout": layout.to_dict(),
            "benchmarks": [
                resident,
                file_based,
                same_frame,
                compatibility_result,
                json_result,
            ],
            "comparisons": {
                "resident_vs_file_vm_speedup": (
                    file_based["mean_seconds"] / resident["mean_seconds"]
                ),
                "same_frame_vs_compatibility_persistence_speedup": (
                    compatibility_result["mean_seconds"] / same_frame["mean_seconds"]
                ),
                "png_checkpoint_cost_vs_resident_vm": (
                    same_frame["mean_seconds"] / resident["mean_seconds"]
                ),
            },
        }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark ZDX spatial PNG paths")
    parser.add_argument("--iterations", type=int, default=50)
    parser.add_argument("--output")
    args = parser.parse_args(argv)

    result = run_benchmarks(args.iterations)
    encoded = json.dumps(result, indent=2, sort_keys=True)
    print(encoded)
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(encoded + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
