"""Consensus foundations for validating distributed compute."""

from dataclasses import dataclass


@dataclass
class ComputeVote:
    node_id: str
    job_id: str
    result_hash: str
    valid: bool


class ComputeConsensus:
    def __init__(self):
        self.votes: list[ComputeVote] = []

    def add_vote(self, vote: ComputeVote) -> None:
        self.votes.append(vote)

    def accepted(self, job_id: str) -> bool:
        votes = [v for v in self.votes if v.job_id == job_id]
        if not votes:
            return False
        valid = sum(1 for v in votes if v.valid)
        return valid > len(votes) / 2
