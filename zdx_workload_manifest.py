"""
ZDX workload manifest foundation.

Describes verified workload metadata without performing execution.
"""

from __future__ import annotations

import time


class ZDXWorkloadManifest:
    def __init__(self, workload_id, frame_manifest):
        self.workload_id = workload_id
        self.frame_manifest = frame_manifest
        self.created = time.time()

    def payload(self):
        return {
            "workload_id": self.workload_id,
            "frame_manifest": self.frame_manifest,
            "created": self.created,
        }
