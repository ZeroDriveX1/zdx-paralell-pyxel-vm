"""Deterministic failure injection for validation and recovery tests."""

from __future__ import annotations

import threading


class InjectedFailure(RuntimeError):
    pass


class FailureInjector:
    def __init__(self, failures=None):
        self._remaining = dict(failures or {})
        self._lock = threading.Lock()

    def hit(self, point):
        with self._lock:
            if point not in self._remaining:
                return
            remaining = self._remaining[point]
            if remaining <= 1:
                del self._remaining[point]
                raise InjectedFailure(f"injected failure at {point}")
            self._remaining[point] = remaining - 1

    def pending(self):
        with self._lock:
            return dict(self._remaining)
