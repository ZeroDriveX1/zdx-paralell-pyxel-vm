# ZDX Parallel Pyxel VM architecture

ZDX separates deterministic VM execution from authenticated transport,
capability discovery, resource admission, durable queue state, and artifact
mobility. The distributed unit is a bounded content-addressed frame task, not a
shared physical-memory process.

## Runtime layers

- `zdx_parallel_vm.py`: deterministic local Pyxel VM execution and existing
  isolation boundaries.
- `zdx_compute_core.py` / `zdx_compute.py`: durable queue, worker leases,
  RAM/CPU admission, timeout/failure recovery, and contribution accounting.
- `zdx_resource_policy.py`: disabled-by-default idle/charging/guard/protected-
  process policy with immediate preemption support.
- `zdx_capabilities.py`: Linux profile discovery and conservative safe limits.
- `zdx_artifacts_core.py` / `zdx_artifacts.py`: encrypted immutable blobs,
  task/peer grants, chunk sessions, checkpoint/resume, and final hash checks.
- `zdx_network.py` / `zdx_node/`: bounded envelope framing and authenticated
  client transport.
- `zdx_server_core.py` → `zdx_server_gossip_core.py` →
  `zdx_server_chunked.py` → `zdx_server.py`: server composition preserving the
  existing compute handlers while adding gossip and chunk mobility.
- `zdx_cluster.py`: karmic master election and operation-log state materializer.
- `zdx_gossip.py`: persistent-sequence signed peer push/pull exchange.

## Election and replicated state

Every server joins the existing karmic/gravitational cluster and calculates the
leader from the existing reputation/mass rules. No server address is embedded
as a fixed coordinator. Peer endpoint lists are bootstrap routes only. A
cluster remains capped at 100 nodes and multi-cluster membership is preserved.

Queue, lease, result, failure, and contribution changes are operation records
with integrity hashes. Peers continuously exchange snapshots. Rejoin merges the
operation union and orders it deterministically, so master changes and
partitions do not require manual queue recovery.

## Resource safety

Local admission and Linux systemd/cgroup enforcement are intentionally
independent. Policy checks protect device/application workloads; CPU quota,
memory high/max, task count, low CPU/I/O weight, nice level, protected paths,
OOM stop behavior, and the production guard provide hard boundaries. A yielded
or expired lease returns to replicated queue state.

## Android boundary

The Android service uses the same authenticated envelope and task-scoped
artifact semantics. It detects CPU/RAM/charging/GPU/NPU capability facts and
persists safe settings. It can receive, resume, verify, and return a bounded
artifact probe. It fails closed for ordinary Pyxel workloads because the APK
does not embed the Python/Pyxel VM; Android is not advertised as a full Pyxel
worker until that signed adapter and device validation exist.
