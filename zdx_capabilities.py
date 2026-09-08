"""Portable capability discovery and safe per-node resource profiles."""

from __future__ import annotations

import argparse
import json
import os
import platform
import tempfile
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from zdx_resource_policy import ResourceSnapshot, current_resource_snapshot


@dataclass
class NodeCapabilities:
    node_id: str
    os: str
    architecture: str
    cpu_count: int
    gpu: bool
    npu: bool
    vm_features: list[str]
    memory_mb: int = 0
    available_memory_mb: int = 0
    recommended_memory_mb: int = 256
    recommended_min_free_memory_mb: int = 512
    recommended_max_concurrent_tasks: int = 1
    profile_version: int = 1

    def to_payload(self) -> dict:
        return asdict(self)


def safe_limits(snapshot: ResourceSnapshot) -> dict:
    """Derive conservative limits from hardware without granting full host use."""
    total = max(0, snapshot.total_memory_mb)
    available = max(0, snapshot.available_memory_mb)
    min_free = max(512, int(total * 0.20)) if total else 512
    memory = max(128, min(4096, int(total * 0.25) if total else 128))
    if available:
        memory = min(memory, max(128, available - min_free))
    concurrency = max(1, min(4, max(1, snapshot.cpu_count // 2)))
    return {
        "memory_limit_mb": memory,
        "min_free_memory_mb": min_free,
        "max_concurrent_tasks": concurrency,
        "idle_cpu_percent": 20.0,
        "idle_only": True,
    }


def detect_capabilities(node_id: str | None = None, snapshot: ResourceSnapshot | None = None) -> NodeCapabilities:
    snapshot = snapshot or current_resource_snapshot()
    limits = safe_limits(snapshot)
    return NodeCapabilities(
        node_id=node_id or str(uuid.uuid4()),
        os=platform.system(),
        architecture=platform.machine(),
        cpu_count=snapshot.cpu_count,
        gpu=False,
        npu=False,
        vm_features=["pyxel-vm", "frame-hash", "deterministic-execution", "encrypted-artifacts"],
        memory_mb=snapshot.total_memory_mb,
        available_memory_mb=snapshot.available_memory_mb,
        recommended_memory_mb=limits["memory_limit_mb"],
        recommended_min_free_memory_mb=limits["min_free_memory_mb"],
        recommended_max_concurrent_tasks=limits["max_concurrent_tasks"],
    )


def capability_message(node_id: str | None = None):
    from zdx_network import ZDXMessage
    return ZDXMessage(kind="capability_report", payload=detect_capabilities(node_id).to_payload())


def write_profile(path: str, node_id: str) -> NodeCapabilities:
    profile = detect_capabilities(node_id)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(profile.to_payload(), stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        target.chmod(0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return profile


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Discover and persist the local ZDX capability profile")
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--output", default=".zdx/capability_profile.json")
    args = parser.parse_args(argv)
    profile = write_profile(args.output, args.node_id)
    print(json.dumps(profile.to_payload(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
