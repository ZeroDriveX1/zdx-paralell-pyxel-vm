"""Reputation tracking for decentralized compute contributors."""

from dataclasses import dataclass


@dataclass
class NodeReputation:
    node_id: str
    verified_cycles: int = 0
    successful_jobs: int = 0
    failed_jobs: int = 0
    reliability: float = 0.0

    def update(self, cycles: int, success: bool) -> None:
        self.verified_cycles += cycles
        if success:
            self.successful_jobs += 1
        else:
            self.failed_jobs += 1
        total = self.successful_jobs + self.failed_jobs
        self.reliability = self.successful_jobs / total if total else 0.0
