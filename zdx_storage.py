"""Canonical transactional persistence for ZeroDriveX.

The module supplies:

* advisory process locks with bounded waits and crash-safe OS release;
* same-directory temporary commits, file and directory fsync, and backups;
* checksummed/versioned JSON state envelopes;
* corruption quarantine and backup recovery;
* sequential, registered schema migrations.
"""

from __future__ import annotations

import base64
import contextlib
import errno
import hashlib
import json
import os
import secrets
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Optional
from zdx_metrics import METRICS

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows is not a supported runtime yet
    fcntl = None


FORMAT_NAME = "zdx-state"
ENVELOPE_VERSION = 1


class StorageError(RuntimeError):
    pass


class LockTimeout(StorageError):
    pass


class CorruptStateError(StorageError):
    pass


class MigrationError(StorageError):
    pass


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise StorageError("state is not canonical JSON") from exc


def _checksum(meta_without_checksum: dict, data: Any) -> str:
    return hashlib.sha256(_canonical({
        "metadata": meta_without_checksum,
        "data": data,
    })).hexdigest()


def _utc_timestamp() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


class FileLock:
    """Reader/writer advisory lock.

    Lock ownership is held by the kernel, so a crashed process cannot leave a
    live lock. The lock file contains diagnostic PID/time metadata; stale
    metadata is replaced after the next successful acquisition.
    """

    def __init__(self, path: str | Path, timeout: float = 10.0,
                 poll_interval: float = 0.02):
        if timeout < 0:
            raise ValueError("lock timeout cannot be negative")
        self.path = Path(path)
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._handle = None

    def acquire(self, exclusive: bool = True) -> "FileLock":
        if self._handle is not None:
            return self
        if fcntl is None:
            raise StorageError("process-safe locking requires fcntl")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")
        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(handle.fileno(), operation | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                METRICS.increment("lock_contention")
                METRICS.increment("transaction_retries")
                if time.monotonic() >= deadline:
                    handle.close()
                    raise LockTimeout(f"timed out acquiring lock {self.path}")
                time.sleep(self.poll_interval)
        if exclusive:
            handle.seek(0)
            handle.truncate()
            handle.write(_canonical({
                "pid": os.getpid(),
                "acquired_at": _utc_timestamp(),
            }))
            handle.flush()
            os.fsync(handle.fileno())
        self._handle = handle
        return self

    def release(self) -> None:
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None

    def __enter__(self) -> "FileLock":
        return self.acquire()

    def __exit__(self, *_args) -> None:
        self.release()

    @contextlib.contextmanager
    def shared(self) -> Iterator["FileLock"]:
        self.acquire(exclusive=False)
        try:
            yield self
        finally:
            self.release()


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_bytes(path: str | Path, payload: bytes, *,
                       mode: int = 0o600, keep_backup: bool = True,
                       lock_timeout: float = 10.0,
                       _already_locked: bool = False,
                       failure_injector=None) -> None:
    """Commit bytes atomically, preserving the previous valid generation."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(target) + ".lock", lock_timeout)
    manager = contextlib.nullcontext() if _already_locked else lock
    started = time.perf_counter()
    with manager:
        temporary = None
        backup = Path(str(target) + ".bak")
        moved_old = False
        try:
            descriptor, temp_name = tempfile.mkstemp(
                prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
            )
            temporary = Path(temp_name)
            try:
                os.fchmod(descriptor, mode)
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                if failure_injector:
                    failure_injector.hit("commit.after_temp_fsync")
            except Exception:
                with contextlib.suppress(OSError):
                    os.close(descriptor)
                raise
            if target.exists() and keep_backup:
                os.replace(target, backup)
                moved_old = True
                if failure_injector:
                    failure_injector.hit("commit.after_backup")
            if failure_injector:
                failure_injector.hit("commit.before_replace")
            os.replace(temporary, target)
            temporary = None
            os.chmod(target, mode)
            _fsync_directory(target.parent)
            METRICS.increment("persistence_commits")
            METRICS.observe("commit_latency", time.perf_counter() - started)
        except Exception:
            METRICS.increment("failed_commits")
            if moved_old and not target.exists() and backup.exists():
                os.replace(backup, target)
                _fsync_directory(target.parent)
            raise
        finally:
            if temporary is not None:
                with contextlib.suppress(FileNotFoundError):
                    temporary.unlink()


@dataclass(frozen=True)
class Migration:
    schema: str
    from_version: int
    to_version: int
    migrate: Callable[[Any], Any]
    rollback: Optional[Callable[[Any], Any]] = None
    name: str = ""


class MigrationRegistry:
    """Registry requiring a complete sequence of one-version migrations."""

    def __init__(self):
        self._migrations: dict[tuple[str, int], Migration] = {}

    def register(self, migration: Migration) -> None:
        if migration.to_version != migration.from_version + 1:
            raise ValueError("migrations must advance exactly one version")
        key = (migration.schema, migration.from_version)
        if key in self._migrations:
            raise ValueError(f"migration already registered: {key}")
        self._migrations[key] = migration

    def upgrade(self, schema: str, data: Any, source: int, target: int
                ) -> tuple[Any, list[dict]]:
        if source > target:
            raise MigrationError("state is newer than this reader")
        current = data
        applied: list[tuple[Migration, Any]] = []
        log: list[dict] = []
        try:
            while source < target:
                migration = self._migrations.get((schema, source))
                if migration is None:
                    raise MigrationError(
                        f"missing migration for {schema} {source}->{source + 1}"
                    )
                before = current
                current = migration.migrate(current)
                applied.append((migration, before))
                log.append({
                    "name": migration.name or (
                        f"{schema}:{migration.from_version}->{migration.to_version}"
                    ),
                    "from": migration.from_version,
                    "to": migration.to_version,
                    "applied_at": _utc_timestamp(),
                })
                source = migration.to_version
        except Exception as exc:
            for migration, before in reversed(applied):
                if migration.rollback is not None:
                    with contextlib.suppress(Exception):
                        migration.rollback(before)
            if isinstance(exc, MigrationError):
                raise
            raise MigrationError("migration failed; original state retained") from exc
        return current, log


DEFAULT_MIGRATIONS = MigrationRegistry()


class StateStore:
    """Atomic, checksummed JSON state for one schema."""

    def __init__(
        self,
        path: str | Path,
        schema: str,
        version: int = 1,
        migrations: MigrationRegistry | None = None,
        lock_timeout: float = 10.0,
        compatibility: Optional[dict] = None,
        failure_injector=None,
    ):
        if version < 1:
            raise ValueError("schema version must be positive")
        self.path = Path(path)
        self.schema = schema
        self.version = version
        self.migrations = migrations or DEFAULT_MIGRATIONS
        self.lock_timeout = lock_timeout
        self.compatibility = compatibility or {
            "format": FORMAT_NAME,
            "envelope_version": ENVELOPE_VERSION,
            "min_reader_version": 1,
        }
        self.last_recovery: Optional[dict] = None
        self.failure_injector = failure_injector

    @property
    def lock_path(self) -> Path:
        return Path(str(self.path) + ".lock")

    @property
    def backup_path(self) -> Path:
        return Path(str(self.path) + ".bak")

    def _envelope(self, data: Any, *, created_at: Optional[str] = None,
                  migration_log: Optional[list] = None) -> dict:
        now = _utc_timestamp()
        metadata = {
            "format": FORMAT_NAME,
            "envelope_version": ENVELOPE_VERSION,
            "schema": self.schema,
            "schema_version": self.version,
            "created_at": created_at or now,
            "updated_at": now,
            "compatibility": dict(self.compatibility),
            "migrations": list(migration_log or []),
        }
        metadata["checksum"] = _checksum(metadata, data)
        return {"metadata": metadata, "data": data}

    def _decode(self, raw: bytes) -> tuple[Any, dict, bool]:
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CorruptStateError("state is not valid UTF-8 JSON") from exc
        if not isinstance(document, dict):
            raise CorruptStateError("state root must be an object")
        if set(document) != {"metadata", "data"}:
            # Legacy version zero: raw JSON object/value without an envelope.
            return document, {
                "schema": self.schema,
                "schema_version": 0,
                "created_at": _utc_timestamp(),
                "migrations": [],
            }, True
        metadata = document["metadata"]
        if not isinstance(metadata, dict):
            raise CorruptStateError("state metadata must be an object")
        required = {
            "format", "envelope_version", "schema", "schema_version",
            "created_at", "updated_at", "compatibility", "migrations", "checksum",
        }
        if not required.issubset(metadata):
            raise CorruptStateError("state metadata is incomplete")
        if metadata["format"] != FORMAT_NAME or metadata["schema"] != self.schema:
            raise CorruptStateError("state schema/format mismatch")
        claimed = metadata["checksum"]
        unsigned = dict(metadata)
        unsigned.pop("checksum", None)
        if not secrets.compare_digest(str(claimed), _checksum(unsigned, document["data"])):
            raise CorruptStateError("state checksum mismatch")
        return document["data"], metadata, False

    def _quarantine(self, path: Path, reason: str) -> Path:
        suffix = f".corrupt.{int(time.time())}.{secrets.token_hex(4)}"
        quarantine = Path(str(path) + suffix)
        os.replace(path, quarantine)
        _fsync_directory(path.parent)
        self.last_recovery = {
            "quarantined": str(quarantine),
            "reason": reason,
            "at": _utc_timestamp(),
        }
        METRICS.increment("recovery_operations")
        return quarantine

    def load(self, default: Any = None, *, auto_migrate: bool = True) -> Any:
        with FileLock(self.lock_path, self.lock_timeout).acquire(exclusive=True):
            return self._load_locked(default, auto_migrate)

    def _load_locked(self, default: Any, auto_migrate: bool) -> Any:
        for temporary in self.path.parent.glob(f".{self.path.name}.*.tmp"):
            with contextlib.suppress(OSError):
                temporary.unlink()
        source = self.path
        if not source.exists() and self.backup_path.exists():
            source = self.backup_path
        if not source.exists():
            return default
        try:
            data, metadata, legacy = self._decode(source.read_bytes())
        except CorruptStateError as primary_error:
            self._quarantine(source, str(primary_error))
            if source != self.backup_path and self.backup_path.exists():
                try:
                    data, metadata, legacy = self._decode(
                        self.backup_path.read_bytes()
                    )
                    atomic_write_bytes(
                        self.path, self.backup_path.read_bytes(),
                        lock_timeout=self.lock_timeout, _already_locked=True,
                    )
                    self.last_recovery["recovered_from"] = str(self.backup_path)
                except CorruptStateError as backup_error:
                    self._quarantine(self.backup_path, str(backup_error))
                    raise CorruptStateError(
                        "primary and backup state are corrupt"
                    ) from backup_error
            elif default is not None:
                return default
            else:
                raise
        source_version = int(metadata.get("schema_version", 0))
        if source_version > self.version:
            raise MigrationError("state schema is newer than this reader")
        if source_version < self.version:
            if not auto_migrate:
                return data
            migration_log = []
            migrated = data
            if source_version == 0:
                migration_log.append({
                    "name": f"{self.schema}:legacy-envelope",
                    "from": 0,
                    "to": 1,
                    "applied_at": _utc_timestamp(),
                })
                source_version = 1
            if source_version < self.version:
                if self.migrations is DEFAULT_MIGRATIONS:
                    from zdx_migrations import discover
                    discover()
                migrated, sequential_log = self.migrations.upgrade(
                    self.schema, migrated, source_version, self.version
                )
                migration_log.extend(sequential_log)
                METRICS.increment("migrations", len(sequential_log))
            envelope = self._envelope(
                migrated,
                created_at=metadata.get("created_at"),
                migration_log=list(metadata.get("migrations", [])) + migration_log,
            )
            atomic_write_bytes(
                self.path, _canonical(envelope), lock_timeout=self.lock_timeout,
                _already_locked=True,
                failure_injector=self.failure_injector,
            )
            return migrated
        return data

    def save(self, data: Any) -> None:
        with FileLock(self.lock_path, self.lock_timeout):
            created_at = None
            migrations = []
            if self.path.exists():
                try:
                    _, metadata, _ = self._decode(self.path.read_bytes())
                    created_at = metadata.get("created_at")
                    migrations = metadata.get("migrations", [])
                except CorruptStateError as exc:
                    self._quarantine(self.path, str(exc))
            envelope = self._envelope(
                data, created_at=created_at, migration_log=migrations
            )
            atomic_write_bytes(
                self.path, _canonical(envelope), lock_timeout=self.lock_timeout,
                _already_locked=True,
                failure_injector=self.failure_injector,
            )

    def update(self, transform: Callable[[Any], Any], default: Any = None) -> Any:
        """Perform a process-safe read/modify/write transaction."""
        with FileLock(self.lock_path, self.lock_timeout):
            current = self._load_locked(default, auto_migrate=True)
            updated = transform(current)
            created_at = None
            migrations = []
            if self.path.exists():
                _, metadata, _ = self._decode(self.path.read_bytes())
                created_at = metadata.get("created_at")
                migrations = metadata.get("migrations", [])
            atomic_write_bytes(
                self.path,
                _canonical(self._envelope(
                    updated, created_at=created_at, migration_log=migrations
                )),
                lock_timeout=self.lock_timeout,
                _already_locked=True,
                failure_injector=self.failure_injector,
            )
            return updated


def atomic_write_secret(path: str | Path, payload: bytes,
                        lock_timeout: float = 10.0) -> None:
    """Atomic 0600 commit for necessary private key material."""
    atomic_write_bytes(
        path, payload, mode=0o600, keep_backup=False, lock_timeout=lock_timeout
    )
