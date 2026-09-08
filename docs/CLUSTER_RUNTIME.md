# Election-aware cluster runtime

Run one `KarmicZDXServer` instance per enrolled node. Each instance calls the
existing `ZDXKarmaSystem.elect_master_node(cluster_id)`; no host or address is
hard-coded as coordinator. Before serving after a master change, exchange the
authenticated `export_cluster_state()` snapshot with `merge_cluster_state()`.
The merge is idempotent and rebuilds queued, running, completed, and failed
records before work resumes.

```python
from zdx_cluster_server import KarmicZDXServer

server = KarmicZDXServer(
    host="0.0.0.0", port=8765, node_id="vps-01", cluster_id="private-a",
    require_auth=True, trusted_peers=enrolled_public_keys,
    tls_context=private_tls_context,
)
print(server.elected_master, server.cluster.cluster_info())
```

The runtime retains the existing 100-node cluster limit and supports multiple
cluster IDs. State snapshots must be exchanged only over the authenticated TLS
peer channel and should be stored under each node's private state directory.
