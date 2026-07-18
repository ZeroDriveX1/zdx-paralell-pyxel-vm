"""
ZDX node registry.

Combines discovery records with capability-aware tracking.
"""

from __future__ import annotations

import time


class ZDXNodeRegistry:
    def __init__(self):
        self.nodes = {}

    def register(self, node_id, capabilities):
        self.nodes[node_id] = {
            "capabilities": capabilities,
            "last_seen": time.time(),
        }

    def heartbeat(self, node_id):
        if node_id in self.nodes:
            self.nodes[node_id]["last_seen"] = time.time()

    def get(self, node_id):
        return self.nodes.get(node_id)

    def all_nodes(self):
        return self.nodes.copy()
