"""
ZDX node enrollment foundation.

Initial placeholder for authenticated node registration.
"""

from __future__ import annotations

import time


class ZDXEnrollment:
    def __init__(self):
        self.nodes = {}

    def request(self, node_id, capabilities):
        self.nodes[node_id] = {
            "capabilities": capabilities,
            "requested": time.time(),
            "status": "pending",
        }
        return self.nodes[node_id]

    def approve(self, node_id):
        if node_id in self.nodes:
            self.nodes[node_id]["status"] = "approved"
            self.nodes[node_id]["approved"] = time.time()

    def status(self, node_id):
        return self.nodes.get(node_id)
