"""Verified compute contribution ledger primitives."""

from dataclasses import dataclass
from time import time


@dataclass(frozen=True)
class ComputeRecord:
    node_id: str
    job_id: str
    verified_cycles: int
    proof_hash: str
    timestamp: int


class ComputeLedger:
    def __init__(self):
        self.records: list[ComputeRecord] = []

    def add_verified_record(self, record: ComputeRecord) -> None:
        self.records.append(record)

    def node_total(self, node_id: str) -> int:
        return sum(
            record.verified_cycles
            for record in self.records
            if record.node_id == node_id
        )
