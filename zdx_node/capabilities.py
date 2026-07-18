from dataclasses import dataclass, asdict
import os
import platform


@dataclass
class NodeCapabilities:
    cpu_count: int
    system: str
    gpu: str | None = None
    npu: str | None = None

    def export(self):
        return asdict(self)


def discover_capabilities():
    return NodeCapabilities(
        cpu_count=os.cpu_count() or 1,
        system=platform.system(),
    )
