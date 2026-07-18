"""Device/node models attached to Open-Pyxel accounts."""

from dataclasses import dataclass, field
from time import time


@dataclass
class DeviceNode:
    node_id: str
    user_id: str
    public_key: str
    hardware_profile: dict
    uptime: int = 0
    reputation: float = 0.0
    last_seen: int = field(default_factory=lambda: int(time()))
    verified_cycles: int = 0

    def record_compute(self, cycles: int) -> None:
        self.verified_cycles += cycles

    def heartbeat(self) -> None:
        self.last_seen = int(time())
