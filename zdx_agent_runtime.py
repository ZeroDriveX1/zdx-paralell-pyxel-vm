"""Registry-driven ZDX agent module for standard and spatial Pyxel execution.

The agent module remains separate from ZDX AgentCore. Same-frame spatial
execution is owned by SpatialAgentSession so frame, VM generation, ABI,
mailboxes, recovery, and checkpoint lifecycle have one explicit owner.
"""

from __future__ import annotations

import copy
import os

from pyxel_registry import PyxelRegistry
from zdx_agent_abi import SpatialAgentABI
from zdx_spatial_agent_session import SpatialAgentSession


class ZDXAgentRuntime:
    def __init__(self, registry: PyxelRegistry, *, checkpoint_interval: int = 10):
        self.registry = registry
        if isinstance(checkpoint_interval, bool) or not isinstance(checkpoint_interval, int) or checkpoint_interval < 1:
            raise ValueError("checkpoint_interval must be a positive integer")
        self.checkpoint_interval = checkpoint_interval
        self._spatial_sessions: dict[str, SpatialAgentSession] = {}
        self._spatial_vm_baseline: dict | None = None

    def _memory(self):
        try:
            return self.registry.get("memory")
        except KeyError:
            return None

    @staticmethod
    def _layout_payload(layout):
        if layout is None:
            return None
        return layout.to_dict() if hasattr(layout, "to_dict") else dict(layout)

    def _state_payload(self, vm, *, spatial_layout=None, source_frame=None):
        marker = vm.checkpoint_marker() if hasattr(vm, "checkpoint_marker") else None
        payload = {
            "shared_state": dict(vm.shared),
            "register_state": {name: dict(values) for name, values in vm.registers.items()},
        }
        if marker is not None:
            payload["vm_checkpoint"] = marker
        layout_payload = self._layout_payload(spatial_layout)
        if layout_payload is not None:
            payload["spatial_layout"] = layout_payload
        if source_frame is not None:
            payload["source_frame"] = os.path.abspath(source_frame)
        return payload

    def _persist_state(self, vm, mem, *, spatial_layout=None, source_frame=None):
        if mem is None:
            return
        payload = self._state_payload(
            vm, spatial_layout=spatial_layout, source_frame=source_frame
        )
        update = getattr(mem, "update", None)
        if callable(update):
            update(payload)
        else:
            for key, value in payload.items():
                mem.remember(key, value)

    def _baseline_for(self, vm) -> dict:
        if self._spatial_vm_baseline is None:
            self._spatial_vm_baseline = SpatialAgentSession.capture_vm_state(vm)
        return copy.deepcopy(self._spatial_vm_baseline)

    def open_spatial_session(
        self,
        image_path: str,
        *,
        abi: SpatialAgentABI | None = None,
    ) -> SpatialAgentSession:
        """Return/create the resident same-frame session for image_path."""
        key = os.path.abspath(image_path)
        existing = self._spatial_sessions.get(key)
        if existing is not None:
            if abi is not None and existing.abi.to_dict() != abi.to_dict():
                raise ValueError("resident session already uses a different agent ABI")
            return existing

        vm = self.registry.get("vm")
        if not callable(getattr(vm, "execute_spatial_frame", None)):
            raise TypeError("registered VM does not support resident spatial execution")
        layout = getattr(vm, "layout", None)
        if layout is None:
            raise TypeError("spatial VM must expose a layout")

        mem = self._memory()
        if mem is None or not getattr(mem, "is_spatial", False):
            raise RuntimeError("resident spatial session requires spatial memory")
        memory_layout = getattr(mem, "layout", None)
        if memory_layout != layout:
            raise ValueError("same-frame spatial memory must use the VM's exact layout")
        if os.path.abspath(getattr(mem, "frame_path", "") or "") != key:
            raise ValueError("resident spatial session requires memory bound to the executable frame")

        session = SpatialAgentSession.open(
            image_path=key,
            vm=vm,
            memory=mem,
            checkpoint_interval=self.checkpoint_interval,
            abi=abi,
            initial_vm_state=self._baseline_for(vm),
        )
        self._spatial_sessions[key] = session
        return session

    def checkpoint(self, image_path: str, *, barrier: bool = True) -> dict | None:
        """Persist the latest resident generation for a spatial frame."""
        session = self._spatial_sessions.get(os.path.abspath(image_path))
        if session is None:
            return None
        return session.checkpoint(barrier=barrier)

    def mailbox(self, image_path: str, role: str, *, slot_size: int = 256):
        """Open a Pyxel-native mailbox bound by the session's Agent ABI."""
        return self.open_spatial_session(image_path).mailbox(role, slot_size=slot_size)

    def memory_manager(self, image_path: str, *, policies=None):
        """Open namespaced/quota-aware memory for a resident spatial agent."""
        return self.open_spatial_session(image_path).memory_manager(policies=policies)

    def close(self, *, flush: bool = True):
        for session in list(self._spatial_sessions.values()):
            session.close(flush=flush)
        self._spatial_sessions.clear()

    def run_mission(self, mission: str):
        """Execute a mission through registered scheduler/mission-agent components."""
        scheduler = self.registry.get("scheduler")
        agent = self.registry.get("mission_agent")
        return scheduler.execute_mission(agent, mission)

    def run(self, image_path: str) -> dict:
        """Execute a legacy/linear PNG program and persist resulting VM state."""
        vm = self.registry.get("vm")
        mem = self._memory()
        vm.execute_texture(image_path)
        self._persist_state(vm, mem, source_frame=image_path)
        return vm.registers

    def run_spatial(self, image_path: str) -> dict:
        """Execute a spatial PNG through resident session when memory is same-frame."""
        vm = self.registry.get("vm")
        execute = getattr(vm, "execute_spatial", None)
        if not callable(execute):
            raise TypeError("registered VM does not support spatial PNG execution")
        layout = getattr(vm, "layout", None)
        if layout is None:
            raise TypeError("spatial VM must expose a layout")

        mem = self._memory()
        same_frame = False
        if mem is not None and getattr(mem, "is_spatial", False):
            memory_layout = getattr(mem, "layout", None)
            if memory_layout is not None:
                same_frame = (
                    os.path.abspath(getattr(mem, "frame_path", "") or "")
                    == os.path.abspath(image_path)
                )
                if same_frame and memory_layout != layout:
                    raise ValueError("same-frame spatial memory must use the VM's exact layout")

        if same_frame and callable(getattr(vm, "execute_spatial_frame", None)):
            return self.open_spatial_session(image_path).execute()

        execute(image_path)
        self._persist_state(
            vm,
            mem,
            spatial_layout=layout,
            source_frame=image_path,
        )
        return vm.registers
