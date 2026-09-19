"""Dependency-free operational metrics and resource snapshots."""

from __future__ import annotations

import json
import os
import resource
import threading
import time
from pathlib import Path


class MetricsRegistry:
    def __init__(self):
        self.started = time.monotonic()
        self._counters = {}
        self._gauges = {}
        self._samples = {}
        self._lock = threading.RLock()

    def increment(self, name, value=1):
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + value

    def gauge(self, name, value):
        with self._lock:
            self._gauges[name] = value

    def observe(self, name, seconds):
        with self._lock:
            total, count, maximum = self._samples.get(name, (0.0, 0, 0.0))
            self._samples[name] = (
                total + seconds, count + 1, max(maximum, seconds)
            )

    def snapshot(self):
        with self._lock:
            samples = {
                name: {
                    "count": count,
                    "average_seconds": total / count if count else 0.0,
                    "max_seconds": maximum,
                }
                for name, (total, count, maximum) in self._samples.items()
            }
            result = {
                "uptime_seconds": time.monotonic() - self.started,
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "latencies": samples,
                "resources": resource_snapshot(),
            }
        return result

    def export_json(self, path):
        from zdx_storage import atomic_write_bytes
        atomic_write_bytes(
            path,
            json.dumps(self.snapshot(), sort_keys=True, indent=2).encode(),
        )


def resource_snapshot():
    try:
        descriptors = len(os.listdir("/proc/self/fd"))
    except OSError:
        descriptors = None
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "active_threads": threading.active_count(),
        "open_file_descriptors": descriptors,
        "max_rss_kib": usage.ru_maxrss,
    }


METRICS = MetricsRegistry()
