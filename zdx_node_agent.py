"""
ZDX node agent foundation.

Coordinates identity, capabilities, registry updates, and heartbeat state
for a running node process.
"""

from __future__ import annotations

import time

from zdx_node_identity import ZDXNodeIdentity
from zdx_capabilities import detect_capabilities


class ZDXNodeAgent:
    def __init__(self):
        self.identity = ZDXNodeIdentity()
        self.capabilities = detect_capabilities()
        self.running = False
        self.last_heartbeat = None

    def heartbeat(self):
        self.last_heartbeat = time.time()
        return {
            "node_id": self.identity.node_id,
            "timestamp": self.last_heartbeat,
        }

    def report(self):
        return {
            "identity": self.identity.payload(),
            "capabilities": self.capabilities.to_payload(),
        }

    def start(self):
        self.running = True
        return self.report()

    def stop(self):
        self.running = False
