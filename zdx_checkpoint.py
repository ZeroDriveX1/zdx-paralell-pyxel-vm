"""Deterministic checkpoint hashing and asynchronous spatial checkpointing."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
from dataclasses import dataclass
from pathlib import Path

from zdx_storage import StateStore


CHECKPOINT_HASH_DOMAIN = b"zdx-vm-checkpoint-v1\x00"


def compute_vm_checkpoint_hash(*, generation: int, clock: int, registers: dict, shared: dict) -> str:
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        raise ValueError("generation must be a non-negative integer")
    state = {
        "generation": generation,
        "clock": int(clock),
        "registers": {
            name: {key: int(value) for key, value in sorted(values.items())}
            for name, values in sorted(registers.items())
        },
        "shared": {name: int(value) for name, value in sorted(shared.items())},
    }
    payload = json.dumps(
        state,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(CHECKPOINT_HASH_DOMAIN + payload).hexdigest()


def checkpoint_hash_from_memory(values: dict) -> str:
    marker = values.get("vm_checkpoint")
    registers = values.get("register_state")
    shared = values.get("shared_state")
    if not isinstance(marker, dict) or not isinstance(registers, dict) or not isinstance(shared, dict):
        raise ValueError("checkpoint snapshot is missing VM marker/state")
    return compute_vm_checkpoint_hash(
        generation=marker.get("generation"),
        clock=marker.get("clock", 0),
        registers=registers,
        shared=shared,
    )


@dataclass(frozen=True)
class SpatialCheckpointRequest:
    request_id: int
    frame: object
    values: dict
    generation: int
    checkpoint_hash: str
    barrier: bool = False


class SpatialCheckpointManager:
    """Single-worker coalescing checkpoint writer.

    Non-barrier requests are latest-wins. A worker can never build an unbounded
    queue when persistence is slower than VM execution.
    """

    def __init__(self, store, *, interval: int = 10, manifest_path: str | None = None, initial_artifact_sha256: str | None = None):
        if isinstance(interval, bool) or not isinstance(interval, int) or interval < 1:
            raise ValueError("checkpoint interval must be a positive integer")
        self.store = store
        self.interval = interval
        self.manifest_path = manifest_path or (str(store.frame_path) + ".checkpoint.json")
        self._manifest = StateStore(self.manifest_path, "spatial-checkpoint-manifest")
        self._condition = threading.Condition()
        self._pending: SpatialCheckpointRequest | None = None
        self._closed = False
        self._last_committed_generation = 0
        self._last_committed_hash = ""
        self._next_request_id = 1
        self._last_submitted_request_id = 0
        self._last_committed_request_id = 0
        self._error: Exception | None = None
        self._current_artifact_sha256 = initial_artifact_sha256
        self._worker = threading.Thread(
            target=self._run,
            name="zdx-spatial-checkpoint",
            daemon=True,
        )
        self._worker.start()

    def due(self, generation: int) -> bool:
        return generation > 0 and generation % self.interval == 0

    @property
    def last_committed_generation(self) -> int:
        return self._last_committed_generation

    @property
    def last_committed_hash(self) -> str:
        return self._last_committed_hash

    def submit(self, frame, values: dict, *, generation: int, checkpoint_hash: str, barrier: bool = False):
        actual = checkpoint_hash_from_memory(values)
        if actual != checkpoint_hash:
            raise ValueError("VM checkpoint hash does not match frozen snapshot state")
        frozen_frame = frame.clone()
        frozen_values = copy.deepcopy(values)
        with self._condition:
            if self._closed:
                raise RuntimeError("checkpoint manager is closed")
            if self._error is not None:
                raise RuntimeError("checkpoint worker failed") from self._error

            request_id = self._next_request_id
            self._next_request_id += 1
            request = SpatialCheckpointRequest(
                request_id=request_id,
                frame=frozen_frame,
                values=frozen_values,
                generation=generation,
                checkpoint_hash=checkpoint_hash,
                barrier=bool(barrier),
            )

            if self._pending is None:
                self._pending = request
            elif self._pending.barrier:
                # Never skip an exact pending barrier. The caller must wait.
                self._condition.wait_for(
                    lambda: self._pending is None
                    or self._error is not None
                    or self._closed
                )
                if self._error is not None:
                    raise RuntimeError("checkpoint worker failed") from self._error
                if self._closed:
                    raise RuntimeError("checkpoint manager is closed")
                self._pending = request
            elif request.barrier:
                # Replace an ordinary pending generation with the exact barrier.
                self._pending = request
            elif request.generation < self._pending.generation:
                raise ValueError("checkpoint generation cannot move backward")
            else:
                self._pending = request

            self._last_submitted_request_id = request_id
            self._condition.notify_all()

        if barrier:
            self.wait_for_request(request_id)
        return request_id

    def wait_for_request(self, request_id: int, timeout: float | None = None) -> bool:
        with self._condition:
            ok = self._condition.wait_for(
                lambda: self._last_committed_request_id >= request_id or self._error is not None,
                timeout=timeout,
            )
            if self._error is not None:
                raise RuntimeError("checkpoint worker failed") from self._error
            return bool(ok and self._last_committed_request_id >= request_id)

    def wait_for(self, generation: int, timeout: float | None = None) -> bool:
        with self._condition:
            ok = self._condition.wait_for(
                lambda: self._last_committed_generation >= generation or self._error is not None,
                timeout=timeout,
            )
            if self._error is not None:
                raise RuntimeError("checkpoint worker failed") from self._error
            return bool(ok and self._last_committed_generation >= generation)

    def flush(self, timeout: float | None = None) -> bool:
        with self._condition:
            target = self._last_submitted_request_id
        if target == 0:
            return True
        return self.wait_for_request(target, timeout=timeout)

    def close(self, *, flush: bool = True, timeout: float | None = None):
        if flush:
            self.flush(timeout=timeout)
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        self._worker.join(timeout=timeout)
        if self._worker.is_alive():
            raise TimeoutError("checkpoint worker did not stop")
        if self._error is not None:
            raise RuntimeError("checkpoint worker failed") from self._error

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._pending is not None or self._closed)
                if self._pending is None and self._closed:
                    return
                request = self._pending
                self._pending = None
                self._condition.notify_all()
            try:
                actual = checkpoint_hash_from_memory(request.values)
                if actual != request.checkpoint_hash:
                    raise ValueError("checkpoint worker hash verification failed")
                artifact = self.store.commit_snapshot(
                    request.frame,
                    request.values,
                    vm_generation=request.generation,
                    vm_checkpoint_hash=request.checkpoint_hash,
                    expected_artifact_sha256=self._current_artifact_sha256,
                )
                manifest = {
                    "checkpoint_request_id": request.request_id,
                    "generation": request.generation,
                    "vm_checkpoint_hash": request.checkpoint_hash,
                    "artifact_sha256": artifact["artifact_sha256"],
                    "frame_path": str(Path(self.store.frame_path).resolve()),
                }
                self._manifest.save(manifest)
                with self._condition:
                    self._last_committed_generation = request.generation
                    self._last_committed_hash = request.checkpoint_hash
                    self._last_committed_request_id = request.request_id
                    self._current_artifact_sha256 = artifact["artifact_sha256"]
                    self._condition.notify_all()
            except Exception as exc:
                with self._condition:
                    self._error = exc
                    self._condition.notify_all()
                return
