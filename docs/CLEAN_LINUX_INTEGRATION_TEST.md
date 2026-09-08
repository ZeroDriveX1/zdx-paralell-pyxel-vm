# Clean Linux integration validation

This is the release-gate runbook for two clean Linux machines. It is not
reported as passed unless executed on two newly provisioned hosts with real TLS
certificates, firewall rules, service restarts, and network isolation.

## Inputs

- two clean Linux VPSes, `zdx-a` and `zdx-b`;
- a private CA and server certificates;
- a signed release tree and trusted release manifest;
- enrolled Ed25519 public keys for both nodes and the operator client;
- root access for installation, systemd, firewall, and reboot tests.

## Test sequence

1. Verify the release on the operator machine:

   ```sh
   sha256sum -c release/SHA256SUMS
   python3 packaging/release_manifest.py verify release/release-manifest.json
   ```

2. Install the identical release on both clean machines:

   ```sh
   for host in zdx-a zdx-b; do
     scp -r /path/to/zdx-release "$host:/tmp/zdx-release"
     ssh "$host" 'sudo env ZDX_CA_FILE=/etc/zdx-worker/ca.pem \
       ZDX_COORDINATOR_HOST=zdx-a.internal ZDX_COORDINATOR_PORT=8765 \
       ZDX_NODE_ID=$(hostname -s) /tmp/zdx-release/packaging/install.sh'
   done
   ```

3. Start one `zdx-server` process per host with both public keys and each
   other host as `--gossip-peer`. Start both workers. Record:

   ```sh
   sudo zdxctl status
   sudo zdxctl capabilities
   sudo journalctl -u zdx-worker.service --no-pager -n 200
   ```

   Acceptance: both workers register signed identity/capability data, remain
   disabled until explicitly enabled, and use the generated cgroup limits.

4. Enable both workers, submit an artifact-backed task, and verify completion
   plus a contribution record on both server state views.

5. Restart both workers and the server processes. Verify identity sequence
   persistence, no duplicate operation application, queue reload, lease
   recovery, result retention, and contribution retention.

6. Partition the hosts by blocking only the private ZDX port between them;
   leave SSH and the production API paths untouched. Submit independent test
   operations, then remove the temporary block. Acceptance: automatic gossip
   rejoins, operation hashes deduplicate, queue/lease/result/contribution state
   converges, and no manual merge is run.

7. Start a multi-chunk artifact transfer, interrupt the worker process or
   temporarily block the ZDX port after a partial checkpoint, restore service,
   and verify:

   - the `.part` checkpoint resumes from its prior offset;
   - a fresh grant is obtained after reconnect;
   - the final SHA-256 matches;
   - a corrupted/incomplete checkpoint is discarded;
   - an expired old grant is rejected;
   - the task lease remains recoverable.

8. Reboot each host separately. Verify systemd restart, disabled-by-default
   policy persistence, cgroup limits, worker registration, master election,
   and contribution/lease/result recovery after boot.

9. Capture logs and state summaries without private keys or secrets. Record
   host OS/kernel, systemd/cgroup version, release manifest digest, test times,
   firewall changes, and pass/fail evidence.

## Required evidence

The release report may mark this gate “tested successfully” only with two-host
logs, service status output, state digests before/after restart and rejoin,
artifact transfer offsets/digests, and explicit evidence that SSH/API traffic
was not blocked. Otherwise it belongs under “not testable in this workspace.”
