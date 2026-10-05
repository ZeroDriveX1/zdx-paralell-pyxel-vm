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

from pyxel_registry import PyxelRegistry


class ZDXAgentRuntime:
    def __init__(self, registry: PyxelRegistry):
        self.registry = registry

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
        payload = {
            "shared_state": dict(vm.shared),
            "register_state": {name: dict(values) for name, values in vm.registers.items()},
        }
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
        resident_transaction = getattr(mem, "spatial_transaction", None) if mem is not None else None
        if same_frame and callable(resident_execute) and callable(resident_transaction):
            def execute_and_persist(frame, values):
                resident_execute(frame)
                values.update(self._state_payload(
                    vm,
                    spatial_layout=layout,
                    source_frame=image_path,
                ))
                return vm.registers

            return resident_transaction(execute_and_persist)

        execute(image_path)
        self._persist_state(
            vm,
            mem,
            spatial_layout=layout,
            source_frame=image_path,
        )
        return vm.registers
