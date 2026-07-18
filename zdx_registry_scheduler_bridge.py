"""
ZDX registry to scheduler bridge.

Keeps discovery storage separate from placement decisions.
"""


class ZDXRegistrySchedulerBridge:
    def __init__(self, registry, scheduler):
        self.registry = registry
        self.scheduler = scheduler

    def sync(self):
        for node_id, node in self.registry.all_nodes().items():
            self.scheduler.register_node(node_id, node["capabilities"])

    def select(self):
        return self.scheduler.select_node()
