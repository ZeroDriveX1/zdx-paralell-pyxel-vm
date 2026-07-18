"""
ZDX peer discovery foundation.

Provides a simple registry abstraction for future LAN discovery,
mobile nodes, and distributed device enrollment.
"""

from __future__ import annotations

import time


class ZDXDiscovery:
    def __init__(self, timeout_seconds=120):
        self.timeout_seconds = timeout_seconds
        self.nodes = {}

    def announce(self, node_id, address, capabilities=None):
        self.nodes[node_id] = {
            "address": address,
            "capabilities": capabilities or {},
            "last_seen": time.time(),
        }

    def heartbeat(self, node_id):
        if node_id in self.nodes:
            self.nodes[node_id]["last_seen"] = time.time()

    def active_nodes(self):
        now = time.time()
        return {
            node_id: node
            for node_id, node in self.nodes.items()
            if now - node["last_seen"] <= self.timeout_seconds
        }

    def remove_stale(self):
        self.nodes = self.active_nodes()
