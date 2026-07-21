"""Replay protection for authenticated messages."""

import time


class ReplayGuard:
    def __init__(self, window_seconds: int = 300):
        self.window_seconds = window_seconds
        self.nonces = {}
        self.sequences = {}

    def check(self, node_id: str, nonce: str, sequence: int, timestamp: int):
        now = int(time.time())
        if abs(now - timestamp) > self.window_seconds:
            return False
        if nonce in self.nonces:
            return False
        previous = self.sequences.get(node_id, -1)
        if sequence <= previous:
            return False
        self.nonces[nonce] = timestamp
        self.sequences[node_id] = sequence
        return True
