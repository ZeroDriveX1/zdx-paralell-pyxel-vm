"""Resident lifecycle owner for one Pyxel-native spatial agent frame."""

from __future__ import annotations

import copy
import os
import threading

from zdx_agent_abi import (
    ROLE_MAILBOX_IN,
    ROLE_MAILBOX_OUT,
    ROLE_PERSISTENT_MEMORY,
    ROLE_PROVENANCE,
    SpatialAgentABI,
)
from zdx_agent_capabilities import AgentCapabilityGateway
from zdx_agent_journal import SpatialEventJournal
from zdx_agent_memory_manager import AgentMemoryManager
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
        self._journal: SpatialEventJournal | None = None
        self._memory_manager: AgentMemoryManager | None = None
        self._capability_gateway: AgentCapabilityGateway | None = None
        self._closed = False
        self._dirty = bool(dirty)
        self._dirty_roles: set[str] = set()
        if dirty:
            self._dirty_roles.add(ROLE_PERSISTENT_MEMORY)
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

    def mark_dirty(self, role: str | None = None) -> None:
        self._dirty = True
        if role:
            self._dirty_roles.add(str(role))

    @property
    def dirty_roles(self) -> tuple[str, ...]:
        return tuple(sorted(self._dirty_roles))

    def memory_manager(self) -> AgentMemoryManager:
        with self._lock:
            self._ensure_open()
            if self._memory_manager is None:
                self._memory_manager = AgentMemoryManager(self)
            return self._memory_manager

    def install_capability_gateway(
        self, gateway: AgentCapabilityGateway
    ) -> str | None:
        with self._lock:
            self._ensure_open()
            if not isinstance(gateway, AgentCapabilityGateway):
                raise TypeError("gateway must be an AgentCapabilityGateway")
            manifest = gateway.install(self)
            self._capability_gateway = gateway
            return manifest

    def decide_action(self, *, capability: str, action: str, arguments: dict):
        with self._lock:
            self._ensure_open()
            if self._capability_gateway is None:
                raise RuntimeError("no capability gateway is installed")
            return self._capability_gateway.decide(
                self,
                capability=capability,
                action=action,
                arguments=arguments,
            )

    def journal(self, *, slot_size: int = 384) -> SpatialEventJournal:
        with self._lock:
            self._ensure_open()
            region = self.abi.require_region(ROLE_PROVENANCE)
            if self._journal is None:
                self._journal = SpatialEventJournal(
                    self.frame, region, slot_size=slot_size
                )
                if self._journal.initialized_new:
                    self.mark_dirty(ROLE_PROVENANCE)
            elif self._journal.slot_size != slot_size:
                raise ValueError("journal already opened with a different slot size")
            return self._journal

    def record_event(
        self,
        event_type: str,
        payload: bytes = b"",
        *,
        generation: int | None = None,
    ):
        with self._lock:
            self._ensure_open()
            if self.abi.region_for(ROLE_PROVENANCE) is None:
                return None
            record = self.journal().append(
                event_type,
                payload,
                generation=self.generation if generation is None else generation,
            )
            self.mark_dirty(ROLE_PROVENANCE)
            return record

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
            self.mark_dirty("vm_state")
            self.record_event(
                "vm.execute",
                marker["checkpoint_hash"].encode("ascii"),
                generation=marker["generation"],
            )
            if self.manager.due(marker["generation"]):
                self.manager.submit(
                    self.frame,
                    self.values,
                    generation=marker["generation"],
                    checkpoint_hash=marker["checkpoint_hash"],
                )
                self._dirty = False
                self._dirty_roles.clear()
                self.frame.clear_dirty()
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
                self.mark_dirty(role)
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
            self.mark_dirty(role)
            self.record_event(
                "mailbox.send",
                f"{message.sequence}:{message.sha256}".encode("ascii"),
            )
            return message

    def receive(self, *, role: str = ROLE_MAILBOX_IN):
        with self._lock:
            message = self.mailbox(role).dequeue()
            if message is not None:
                self.mark_dirty(role)
                self.record_event(
                    "mailbox.receive",
                    f"{message.sequence}:{message.sha256}".encode("ascii"),
                )
            return message

    def checkpoint(self, *, barrier: bool = True) -> dict:
        with self._lock:
            self._ensure_open()
            self._activate_vm()
            marker = self._capture_state()
            request_id = self.manager.submit(
                self.frame,
                self.values,
                generation=marker["generation"],
                checkpoint_hash=marker["checkpoint_hash"],
                barrier=barrier,
            )
            self._dirty = False
            self._dirty_roles.clear()
            self.frame.clear_dirty()
            result = copy.deepcopy(marker)
            result["checkpoint_request_id"] = request_id
            if barrier:
                result["artifact_sha256"] = self.manager.last_committed_artifact_sha256
            return result

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
            self._journal = None
            self._memory_manager = None
            self._capability_gateway = None
