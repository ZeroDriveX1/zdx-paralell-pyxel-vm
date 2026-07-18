"""Open-Pyxel user account abstractions.

Accounts represent users. Devices/nodes attached to accounts perform
verified computation independently.
"""

from dataclasses import dataclass, field
from time import time


@dataclass
class Account:
    user_id: str
    public_key: str
    created_at: int = field(default_factory=lambda: int(time()))
    compute_credits: int = 0
    reputation: float = 0.0

    def add_verified_compute(self, cycles: int) -> None:
        self.compute_credits += cycles
