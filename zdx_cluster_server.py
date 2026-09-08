"""Election-aware server adapter for multi-node private deployments."""

from __future__ import annotations

from zdx_cluster import KarmicClusterRuntime
from zdx_compute import ComputeTask
from zdx_server import ZDXServer


class KarmicZDXServer(ZDXServer):
    """Expose queue recovery behind the existing transport server.

    Operators run one instance per enrolled node.  ``elected_master`` is
    calculated from the existing karma/gravitational election on every call;
    no address is treated as a coordinator.  Peer state snapshots are merged
    before a node is promoted to serve work.
    """

    def __init__(self, *args, node_id: str, cluster_id: str = "default",
                 karma_path: str = ".zdx/karma.json",
                 cluster_state_path: str = ".zdx/cluster_state.json", **kwargs):
        super().__init__(*args, **kwargs)
        self.cluster = KarmicClusterRuntime(node_id, cluster_id, karma_path=karma_path, state_path=cluster_state_path)
        self.cluster.join()

    @property
    def elected_master(self):
        return self.cluster.master_id()

    def is_elected_master(self) -> bool:
        return self.cluster.is_master()

    def export_cluster_state(self) -> dict:
        return self.cluster.state.snapshot()

    def merge_cluster_state(self, snapshot: dict) -> int:
        added = self.cluster.merge_peer_state(snapshot)
        if added:
            self.recover_compute_queue()
        return added

    def recover_compute_queue(self) -> None:
        """Rebuild local queue/lease/result state after master change."""
        materialized = self.cluster.state.materialized()
        current = self.compute.status()
        current["queued"] = materialized["queued"]
        current["running"] = materialized["running"]
        current["completed"] = materialized["completed"]
        current["failed"] = materialized["failed"]
        self.compute._state.update({key: current[key] for key in ("queued", "running", "completed", "failed")})
        self.compute._save()

    def submit_replicated_task(self, task: ComputeTask, origin: str) -> None:
        if not self.is_elected_master():
            raise RuntimeError("this node is not the elected karmic master")
        self.cluster.state.queue_task(task.to_dict(), origin)
        self.compute.submit(task)
