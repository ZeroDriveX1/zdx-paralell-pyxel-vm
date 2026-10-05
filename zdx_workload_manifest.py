"""ZDX workload manifest foundation.

A workload manifest describes what a node must support before it accepts a frame.
Spatial PNG requirements are explicit so scheduling can reject incapable nodes
before transfer or execution.
"""

from __future__ import annotations

import time

from zdx_spatial_frame import SPATIAL_FRAME_VERSION, SPATIAL_PNG_FEATURE, SpatialLayout


class ZDXWorkloadManifest:
    def __init__(
        self,
        workload_id,
        frame_manifest,
        *,
        execution_model=None,
        required_vm_features=None,
        spatial_layout=None,
    ):
        self.workload_id = workload_id
        self.frame_manifest = frame_manifest
        self.created = time.time()
        self.execution_model = execution_model
        self.required_vm_features = list(required_vm_features or [])
        self.spatial_layout = self._normalize_layout(spatial_layout)

        frame_payload = self._frame_payload()
        if self.execution_model is None and isinstance(frame_payload, dict):
            self.execution_model = frame_payload.get("execution_model")
        if self.spatial_layout is None and isinstance(frame_payload, dict):
            layout = frame_payload.get("spatial_layout")
            if layout is not None:
                self.spatial_layout = dict(layout)
        if self.execution_model == "spatial-png":
            if self.spatial_layout is None:
                raise ValueError("spatial-png workload requires a spatial_layout")
            if SPATIAL_PNG_FEATURE not in self.required_vm_features:
                self.required_vm_features.append(SPATIAL_PNG_FEATURE)
            if isinstance(frame_payload, dict):
                version = frame_payload.get("spatial_version", SPATIAL_FRAME_VERSION)
                if version != SPATIAL_FRAME_VERSION:
                    raise ValueError(
                        f"unsupported spatial_version {version!r}; "
                        f"expected {SPATIAL_FRAME_VERSION}"
                    )

    @staticmethod
    def _normalize_layout(layout):
        if layout is None:
            return None
        if hasattr(layout, "to_dict"):
            layout = layout.to_dict()
        if isinstance(layout, dict):
            return SpatialLayout.from_dict(layout).to_dict()
        raise TypeError("spatial_layout must be a dict, expose to_dict(), or be None")

    def _frame_payload(self):
        if hasattr(self.frame_manifest, "payload"):
            return self.frame_manifest.payload()
        return self.frame_manifest

    def payload(self):
        payload = {
            "workload_id": self.workload_id,
            "frame_manifest": self._frame_payload(),
            "created": self.created,
        }
        if self.execution_model is not None:
            payload["execution_model"] = self.execution_model
        if self.required_vm_features:
            payload["required_vm_features"] = list(self.required_vm_features)
        if self.spatial_layout is not None:
            payload["spatial_version"] = SPATIAL_FRAME_VERSION
            payload["spatial_layout"] = dict(self.spatial_layout)
        return payload
