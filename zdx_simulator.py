"""
ZDX multi-node simulation harness.

Used for validating registry, scheduler, and capability flows without
requiring physical devices.
"""

from __future__ import annotations

from zdx_node_registry import ZDXNodeRegistry
from zdx_scheduler import ZDXScheduler


class SimulatedNode:
    def __init__(self, node_id, capabilities):
        self.node_id = node_id
        self.capabilities = capabilities


class ZDXSimulator:
    def __init__(self):
        self.registry = ZDXNodeRegistry()
        self.scheduler = ZDXScheduler()
        self.nodes = []

    def add_node(self, node):
        self.nodes.append(node)
        self.registry.register(node.node_id, node.capabilities)
        self.scheduler.register_node(node.node_id, node.capabilities)

    def best_node(self):
        return self.scheduler.select_node()

    def snapshot(self):
        return self.registry.all_nodes()
