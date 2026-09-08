import json

from zdx_cluster import Contribution, KarmicClusterRuntime
from zdx_compute import ComputeTask


def _promote(runtime, node_ids):
    for node_id in node_ids:
        score = runtime.karma.get_score(node_id)
        score.level = "MASTER"
        score.total_experience = 100_000 if node_id == "n1" else 90_000
        score.karma = 80
        runtime.karma.clusters["c"].add(node_id)
    runtime.karma.save()


def test_two_node_state_merge_and_karmic_failover(tmp_path):
    first = KarmicClusterRuntime("n1", "c", karma_path=str(tmp_path / "k1.json"), state_path=str(tmp_path / "s1.json"))
    second = KarmicClusterRuntime("n2", "c", karma_path=str(tmp_path / "k2.json"), state_path=str(tmp_path / "s2.json"))
    assert first.join() and second.join()
    _promote(first, ["n1", "n2"])
    second.karma.scores = first.karma.scores
    second.karma.clusters = first.karma.clusters
    assert first.master_id() == "n1"
    task = ComputeTask(artifact_digest="a" * 64, frame_sha256="a" * 64).to_dict()
    first.state.queue_task(task, "n1")
    assert second.state.merge(first.state.snapshot()) == 1
    lease = second.state.claim_task("n2", "n2", 1024, 1)
    assert lease and lease["task"]["task_id"] == task["task_id"]
    second.state.complete_task(task["task_id"], lease["lease_id"], {"ok": True}, "verified", "n2")
    second.state.record_contribution(Contribution("n2", "w", 1.0, 256.0, "frame", "verified", 1, 8), "n2")
    assert first.state.merge(second.state.snapshot()) >= 2
    assert task["task_id"] in first.state.materialized()["completed"]
    first.karma._mark_eviction("n1")
    assert first.master_id() == "n2"
