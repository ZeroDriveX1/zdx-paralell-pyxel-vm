"""Hardware capability detection foundation."""

import os
from dataclasses import dataclass


@dataclass
class DeviceProfile:
    cpu_cores: int
    memory_available_mb: int
    gpu_available: bool = False


def detect_device() -> DeviceProfile:
    return DeviceProfile(
        cpu_cores=os.cpu_count() or 1,
        memory_available_mb=0,
        gpu_available=False,
    )
