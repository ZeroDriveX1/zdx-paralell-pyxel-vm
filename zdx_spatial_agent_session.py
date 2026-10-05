"""Resident lifecycle owner for one Pyxel-native spatial agent frame."""

from __future__ import annotations

import copy
import os
import threading

from zdx_agent_abi import (
    ROLE_MAILBOX_IN,
    ROLE_MAILBOX_OUT,
    ROLE_PERSISTENT_MEMORY,
    SpatialAgentABI,
)
from zdx_checkpoint import SpatialCheckpointManager, checkpoint_hash_from_memory
from zdx_spatial_mailbox import SpatialMailbox


class SpatialAgentSession:
    """Own one resident frame, VM state, ABI, mailboxes, and checkpoint lifecycle."""

    def __init__(
        self,
        *,
        image_path: str,
        vm,
        memory,
        frame,
        values: dict,
        manager: SpatialCheckpointManager,
        abi: SpatialAgentABI,
        dirty: bool = False,
    ):
        self.image_path = os.path.abspath(image_path)
        self.vm = vm
        self.memory = memory
        self.frame = frame
        self.values = values
        self.manager = manager
        self.abi = abi
        self._mailboxes: dict[str, SpatialMailbox] = {}
        self._closed = False
        self._dirty = bool(dirty)
        self._lock = threading.RLock()

    @staticmethod
    def capture_vm_state(vm) -> dict:
        marker = vm.checkpoint_marker()
        return {
            "vm_checkpoint": copy.deepcopy(marker),
            "register_state": copy.deepcopy(vm.registers),
            "shared_state": copy.deepcopy(vm.shared),
        }

    @classmethod
    def open(
        cls,
        *,
        image_path: str,
        vm,
        memory,
        checkpoint_interval: int = 10,
        abi: SpatialAgentABI | None = None,
        initial_vm_state: dict | None = None,
    ) -> "SpatialAgentSession":
        if not getattr(memory, "is_spatial", False):
            raise TypeError("SpatialAgentSession requires spatial agent memory")
        layout = getattr(vm, "layout", None)
        if layout is None:
            raise TypeError("SpatialAgentSession requires a spatial VM layout")
        memory_layout = getattr(memory, "layout", None)
        if memory_layout != layout:
            raise ValueError("session memory and VM must use the exact same SpatialLayout")
        memory_path = os.path.abspath(getattr(memory, "frame_path", "") or "")
        target_path = os.path.abspath(image_path)
        if memory_path != target_path:
            raise ValueError("SpatialAgentSession requires memory bound to the executable frame")

        store = getattr(memory, "_store", None)
        loader = getattr(store, "load_resident_snapshot", None)
        if not callable(loader):
            raise RuntimeError("spatial memory backend lacks resident snapshot support")
        frame, values, _store_generation, artifact_sha = loader()

        selected_abi = abi or SpatialAgentABI.infer(
            layout,
            persistent_region=getattr(memory, "region", None),
        )
        selected_abi.validate(layout)
        persistent_region = selected_abi.region_for(ROLE_PERSISTENT_MEMORY)
        memory_region = getattr(memory, "region", None)
        if memory_region is None and any(
            item.role != ROLE_PERSISTENT_MEMORY for item in selected_abi.bindings
        ):
            raise ValueError(
                "agent ABI subregions require memory bound to a named persistent region"
            )
        if memory_region is not None and persistent_region != memory_region:
            raise ValueError(
                "agent ABI persistent_memory role must match the spatial memory region"
            )

        persisted_abi = values.get("agent_abi")
        dirty = persisted_abi is None
        if persisted_abi is not None:
            restored_abi = SpatialAgentABI.from_dict(persisted_abi, layout)
            if restored_abi.to_dict() != selected_abi.to_dict():
                raise ValueError("persisted agent ABI does not match requested ABI")
        values["agent_abi"] = selected_abi.to_dict()

        persisted = values.get("vm_checkpoint")
        if persisted is not None:
            vm.restore_checkpoint(
                generation=persisted["generation"],
                clock=persisted.get("clock", 0),
                registers=values["register_state"],
                shared=values["shared_state"],
                checkpoint_hash=persisted["checkpoint_hash"],
            )
            if checkpoint_hash_from_memory(values) != persisted["checkpoint_hash"]:
                raise ValueError("resident checkpoint state failed hash verification")
        else:
            seed = copy.deepcopy(initial_vm_state) if initial_vm_state is not None else cls.capture_vm_state(vm)
            vm.restore_checkpoint(
                generation=seed["vm_checkpoint"]["generation"],
                clock=seed["vm_checkpoint"].get("clock", 0),
                registers=seed["register_state"],
                shared=seed["shared_state"],
                checkpoint_hash=seed["vm_checkpoint"]["checkpoint_hash"],
            )
            values.update(seed)

        metadata = {
            "spatial_layout": layout.to_dict(),
            "source_frame": target_path,
        }
        if any(values.get(key) != value for key, value in metadata.items()):
            dirty = True
        values.update(metadata)
        manager = SpatialCheckpointManager(
            store,
            interval=checkpoint_interval,
            initial_artifact_sha256=artifact_sha,
        )
        return cls(
            image_path=target_path,
            vm=vm,
            memory=memory,
            frame=frame,
            values=values,
            manager=manager,
            abi=selected_abi,
            dirty=dirty,
        )

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("spatial agent session is closed")

    def _activate_vm(self) -> None:
        marker = self.values["vm_checkpoint"]
        current = self.vm.checkpoint_marker()
        if (
            current["generation"] == marker["generation"]
            and current["checkpoint_hash"] == marker["checkpoint_hash"]
        ):
            return
        self.vm.restore_checkpoint(
            generation=marker["generation"],
            clock=marker.get("clock", 0),
            registers=self.values["register_state"],
            shared=self.values["shared_state"],
            checkpoint_hash=marker["checkpoint_hash"],
        )

    def _capture_state(self) -> dict:
        state = self.capture_vm_state(self.vm)
        self.values.update(state)
        self.values["agent_abi"] = self.abi.to_dict()
        self.values["spatial_layout"] = self.vm.layout.to_dict()
        self.values["source_frame"] = self.image_path
        return state["vm_checkpoint"]

    @property
    def generation(self) -> int:
        return int(self.values["vm_checkpoint"]["generation"])

    @property
    def checkpoint_hash(self) -> str:
        return str(self.values["vm_checkpoint"]["checkpoint_hash"])

    def execute(self) -> dict:
        with self._lock:
            self._ensure_open()
            self._activate_vm()
            self.vm.execute_spatial_frame(self.frame)
            marker = self._capture_state()
            self._dirty = True
            if self.manager.due(marker["generation"]):
                self.manager.submit(
                    self.frame,
                    self.values,
                    generation=marker["generation"],
                    checkpoint_hash=marker["checkpoint_hash"],
                )
                self._dirty = False
            return self.vm.registers

    def mailbox(self, role: str, *, slot_size: int = 256) -> SpatialMailbox:
        with self._lock:
            self._ensure_open()
            if role not in (ROLE_MAILBOX_IN, ROLE_MAILBOX_OUT):
                raise ValueError("mailbox role must be mailbox_in or mailbox_out")
            region = self.abi.require_region(role)
            cached = self._mailboxes.get(role)
            if cached is None:
                cached = SpatialMailbox(self.frame, region, slot_size=slot_size)
                self._mailboxes[role] = cached
                self._dirty = True
            elif cached.slot_size != slot_size:
                raise ValueError("mailbox already opened with a different slot size")
            return cached

    def send(
        self,
        payload: bytes,
        *,
        sender: str,
        recipient: str,
        topic: str = "",
        role: str = ROLE_MAILBOX_OUT,
    ):
        with self._lock:
            message = self.mailbox(role).enqueue(
                payload,
                sender=sender,
                recipient=recipient,
                topic=topic,
            )
            self._dirty = True
            return message

    def receive(self, *, role: str = ROLE_MAILBOX_IN):
        with self._lock:
            message = self.mailbox(role).dequeue()
            if message is not None:
                self._dirty = True
            return message

    def checkpoint(self, *, barrier: bool = True) -> dict:
        with self._lock:
            self._ensure_open()
            self._activate_vm()
            marker = self._capture_state()
            self.manager.submit(
                self.frame,
                self.values,
                generation=marker["generation"],
                checkpoint_hash=marker["checkpoint_hash"],
                barrier=barrier,
            )
            self._dirty = False
            return copy.deepcopy(marker)

    def flush(self, timeout: float | None = None) -> bool:
        with self._lock:
            self._ensure_open()
            if self._dirty:
                self.checkpoint(barrier=True)
            return self.manager.flush(timeout=timeout)

    def close(self, *, flush: bool = True, timeout: float | None = None) -> None:
        with self._lock:
            if self._closed:
                return
            if flush and self._dirty:
                self.checkpoint(barrier=True)
            self.manager.close(flush=flush, timeout=timeout)
            self._closed = True
            self._mailboxes.clear()
