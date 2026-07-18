"""
Scheduler integration hooks for ZDX nodes.

Keeps workload placement separate from VM execution.
"""


class ZDXSchedulerHooks:
    def __init__(self, scheduler):
        self.scheduler = scheduler

    def register_capability_report(self, node_id, report):
        self.scheduler.register_node(node_id, report)

    def best_node(self):
        return self.scheduler.select_node()
