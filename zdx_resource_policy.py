"""Persistent, production-safe resource admission policy for ZDX workers.

The policy is deliberately conservative: it is disabled by default, runs only
when the host is idle when requested, and requires a memory reservation for
every task.  It does not expose or merge physical RAM between machines; it
controls how much work a node may admit locally.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class ResourceSnapshot:
    """Point-in-time host resources used for admission decisions."""

    cpu_percent: float
    total_memory_mb: int
    available_memory_mb: int
    cpu_count: int
    charging: bool = True
    battery_percent: int | None = None


@dataclass
class ResourcePolicy:
    """Persistent worker safety limits.

    ``memory_limit_mb`` is the total RAM budget ZDX may reserve on this host;
    ``min_free_memory_mb`` is always kept outside that budget.  ``idle_only``
    prevents new work when system load is above ``idle_cpu_percent`` or when a
    production guard file exists.
    """

    enabled: bool = False
    idle_only: bool = True
    idle_cpu_percent: float = 20.0
    memory_limit_mb: int = 512
    min_free_memory_mb: int = 1024
    max_concurrent_tasks: int = 1
    require_charging: bool = False
    production_guard_file: str = ".zdx/production_busy"
    protected_processes: list[str] = field(default_factory=list)
    nice_level: int = 10
    poll_interval_seconds: float = 10.0

    def validate(self) -> "ResourcePolicy":
        if not 0 <= self.idle_cpu_percent <= 100:
            raise ValueError("idle_cpu_percent must be between 0 and 100")
        if self.memory_limit_mb < 1:
            raise ValueError("memory_limit_mb must be positive")
        if self.min_free_memory_mb < 0:
            raise ValueError("min_free_memory_mb cannot be negative")
        if self.max_concurrent_tasks < 1:
            raise ValueError("max_concurrent_tasks must be positive")
        if not 0 <= self.nice_level <= 19:
            raise ValueError("nice_level must be between 0 and 19")
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        return self

    def available_budget_mb(self, snapshot: ResourceSnapshot) -> int:
        """Return the RAM budget a worker may advertise to the coordinator."""
        return max(
            0,
            min(
                self.memory_limit_mb,
                snapshot.available_memory_mb - self.min_free_memory_mb,
            ),
        )

    def admit(
        self,
        snapshot: ResourceSnapshot,
        required_memory_mb: int = 0,
        protected_processes: Iterable[str] = (),
    ) -> tuple[bool, str]:
        """Decide whether a new task may start on this host."""
        if not self.enabled:
            return False, "worker disabled"
        if required_memory_mb < 0 or required_memory_mb > self.memory_limit_mb:
            return False, "task exceeds configured memory limit"
        if self.require_charging and not snapshot.charging:
            return False, "charging is required"
        if self.production_guard_file and Path(self.production_guard_file).exists():
            return False, "production guard is active"
        protected = {name.lower() for name in self.protected_processes}
        protected.update(name.lower() for name in protected_processes)
        if protected and _protected_process_running(protected):
            return False, "protected production process is running"
        if self.idle_only and snapshot.cpu_percent > self.idle_cpu_percent:
            return False, "host is not idle"
        if required_memory_mb > self.available_budget_mb(snapshot):
            return False, "insufficient safe memory budget"
        return True, "admitted"


class ResourcePolicyStore:
    """Atomic JSON persistence for worker policy settings."""

    def __init__(self, path: str = ".zdx/resource_policy.json"):
        self.path = Path(path)

    def load(self) -> ResourcePolicy:
        if not self.path.exists():
            return ResourcePolicy()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            policy = ResourcePolicy(**data)
            return policy.validate()
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"invalid resource policy {self.path}: {exc}") from exc

    def save(self, policy: ResourcePolicy) -> ResourcePolicy:
        policy.validate()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(asdict(policy), stream, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return policy

    def update(self, **changes) -> ResourcePolicy:
        data = asdict(self.load())
        unknown = set(changes) - set(data)
        if unknown:
            raise ValueError(f"unknown resource policy fields: {sorted(unknown)}")
        data.update(changes)
        return self.save(ResourcePolicy(**data))


def current_resource_snapshot(
    protected_processes: Iterable[str] = (),
) -> ResourceSnapshot:
    """Read portable host resources without requiring a third-party package."""
    cpu_count = max(1, os.cpu_count() or 1)
    try:
        load1 = os.getloadavg()[0]
        cpu_percent = max(0.0, min(100.0, (load1 / cpu_count) * 100.0))
    except (AttributeError, OSError):
        cpu_percent = 0.0

    total_memory_mb, available_memory_mb = _memory_stats()
    return ResourceSnapshot(
        cpu_percent=cpu_percent,
        total_memory_mb=total_memory_mb,
        available_memory_mb=available_memory_mb,
        cpu_count=cpu_count,
        charging=True,
    )


def _memory_stats() -> tuple[int, int]:
    try:
        with open("/proc/meminfo", encoding="utf-8") as stream:
            values = {}
            for line in stream:
                key, _, raw = line.partition(":")
                if key in {"MemTotal", "MemAvailable", "MemFree"}:
                    values[key] = int(raw.strip().split()[0])
            total = max(0, values.get("MemTotal", 0) // 1024)
            available = values.get("MemAvailable", values.get("MemFree", 0))
            return total, max(0, available // 1024)
    except (OSError, ValueError):
        pass

    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        pages = os.sysconf("SC_AVPHYS_PAGES")
        available = max(0, (page_size * pages) // (1024 * 1024))
        return available, available
    except (ValueError, OSError):
        return 0, 0


def _protected_process_running(names: set[str]) -> bool:
    proc = Path("/proc")
    if not proc.exists():
        return False
    current_pid = str(os.getpid())
    for entry in proc.iterdir():
        if not entry.name.isdigit() or entry.name == current_pid:
            continue
        try:
            command = (entry / "comm").read_text(encoding="utf-8").strip().lower()
            cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                errors="ignore"
            ).lower()
        except OSError:
            continue
        if any(name in command or name in cmdline for name in names):
            return True
    return False
