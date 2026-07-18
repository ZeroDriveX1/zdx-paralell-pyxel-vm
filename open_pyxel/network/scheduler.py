"""Fair compute allocation scheduler foundation."""

from dataclasses import dataclass


@dataclass
class TrainingRequest:
    job_id: str
    required_nodes: int
    priority: float = 0.0


class ComputeScheduler:
    def __init__(self):
        self.queue: list[TrainingRequest] = []

    def submit(self, request: TrainingRequest) -> None:
        self.queue.append(request)
        self.queue.sort(key=lambda job: job.priority, reverse=True)

    def next_job(self) -> TrainingRequest | None:
        if not self.queue:
            return None
        return self.queue.pop(0)
