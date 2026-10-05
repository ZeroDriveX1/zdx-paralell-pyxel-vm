"""Registry-driven ZDX agent module for standard and spatial Pyxel execution.

The agent module remains separate from ZDX AgentCore. It coordinates registered
VM, memory, scheduler, and inference-facing components without coupling the VM
to a specific agent implementation.

For spatial execution, the PNG raster remains the executable/state container.
Agent memory may live in a separate spatial PNG or in a named non-executable
region of the same executable frame.
"""

from __future__ import annotations

import os

from zdx_checkpoint import SpatialCheckpointManager, checkpoint_hash_from_memory

from pyxel_registry import PyxelRegistry


class ZDXAgentRuntime:
    def __init__(self, registry: PyxelRegistry, *, checkpoint_interval: int = 10):
        self.registry = registry
        if isinstance(checkpoint_interval, bool) or not isinstance(checkpoint_interval, int) or checkpoint_interval < 1:
            raise ValueError("checkpoint_interval must be a positive integer")
        self.checkpoint_interval = checkpoint_interval
        self._spatial_sessions = {}

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

    def checkpoint(self, image_path: str, *, barrier: bool = True) -> dict | None:
        """Persist the latest resident generation for a spatial frame."""
        session = self._spatial_sessions.get(os.path.abspath(image_path))
        if session is None:
            return None
        vm = self.registry.get("vm")
        marker = vm.checkpoint_marker()
        session["manager"].submit(
            session["frame"],
            session["values"],
            generation=marker["generation"],
            checkpoint_hash=marker["checkpoint_hash"],
            barrier=barrier,
        )
        return marker

    def close(self, *, flush: bool = True):
        for session in list(self._spatial_sessions.values()):
            session["manager"].close(flush=flush)
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
        """Execute a spatial PNG and persist state through the spatial contract.

        The registered VM must expose execute_spatial() and layout. When the
        registered memory backend is bound to the same PNG, its named region
        must be outside the executable rows; SpatialLayout validation enforces
        that boundary.
        """
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
                left = self._layout_payload(layout)
                right = self._layout_payload(memory_layout)
                same_frame = (
                    os.path.abspath(getattr(mem, "frame_path", "") or "")
                    == os.path.abspath(image_path)
                )
                if same_frame and left != right:
                    raise ValueError("same-frame spatial memory must use the VM's exact layout")

        resident_execute = getattr(vm, "execute_spatial_frame", None)
        if same_frame and callable(resident_execute):
            session_key = os.path.abspath(image_path)
            session = self._spatial_sessions.get(session_key)
            if session is None:
                store = getattr(mem, "_store", None)
                loader = getattr(store, "load_resident_snapshot", None)
                if not callable(loader):
                    raise RuntimeError("same-frame spatial memory requires resident snapshot support")
                frame, values, _store_generation, artifact_sha = loader()
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
                manager = SpatialCheckpointManager(
                    store,
                    interval=self.checkpoint_interval,
                    initial_artifact_sha256=artifact_sha,
                )
                session = {"frame": frame, "values": values, "manager": manager}
                self._spatial_sessions[session_key] = session

            resident_execute(session["frame"])
            session["values"].update(self._state_payload(
                vm,
                spatial_layout=layout,
                source_frame=image_path,
            ))
            marker = vm.checkpoint_marker()
            manager = session["manager"]
            if manager.due(marker["generation"]):
                manager.submit(
                    session["frame"],
                    session["values"],
                    generation=marker["generation"],
                    checkpoint_hash=marker["checkpoint_hash"],
                )
            return vm.registers

        execute(image_path)
        self._persist_state(
            vm,
            mem,
            spatial_layout=layout,
            source_frame=image_path,
        )
        return vm.registers
