"""Resource availability monitoring foundation."""

from dataclasses import dataclass


@dataclass
class ResourceState:
    cpu_percent: float
    memory_percent: float
    battery_available: bool = True


def current_resources() -> ResourceState:
    return ResourceState(
        cpu_percent=0.0,
        memory_percent=0.0,
        battery_available=True,
    )
