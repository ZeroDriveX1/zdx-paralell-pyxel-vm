"""Runtime configuration for Open-Pyxel nodes."""

from dataclasses import dataclass


@dataclass
class NodeConfig:
    cpu_limit: int = 50
    gpu_enabled: bool = False
    max_memory_mb: int = 4096
    battery_mode: str = "paused"
    network_enabled: bool = True
