"""
ZDX node capability reporting.

Provides a portable description of node resources without executing remote
workloads. Used for future scheduling and discovery.
"""

from __future__ import annotations

import os
import platform
import uuid
from dataclasses import dataclass, asdict


@dataclass
class NodeCapabilities:
    node_id: str
    os: str
    architecture: str
    cpu_count: int
    gpu: bool
    npu: bool
    vm_features: list[str]

    def to_payload(self):
        return asdict(self)


def detect_capabilities() -> NodeCapabilities:
    return NodeCapabilities(
        node_id=str(uuid.uuid4()),
        os=platform.system(),
        architecture=platform.machine(),
        cpu_count=os.cpu_count() or 1,
        gpu=False,
        npu=False,
        vm_features=["pyxel-vm", "frame-hash", "deterministic-execution"],
    )


def capability_message():
    from zdx_network import ZDXMessage

    return ZDXMessage(
        kind="capability_report",
        payload=detect_capabilities().to_payload(),
    )
