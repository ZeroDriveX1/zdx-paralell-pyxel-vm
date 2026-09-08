# ZDX Pyxel private production user guide

This is the operator guide for a private ZDX Pyxel fabric on Linux/VPS nodes
and Android devices. It covers release verification, enrollment, installation,
capability discovery, automatic state gossip, queue use, artifact mobility,
resource safety, Android setup, updates, and troubleshooting.

## 1. Operating model

ZDX distributes bounded, verified workloads. It does not merge physical RAM
into one shared address space: every node reserves and enforces its own local
CPU/RAM budget. Device and production application workloads have priority.

The existing karmic/gravitational election remains authoritative. There is no
fixed coordinator. Every server process joins the cluster, reports its karma,
and calculates the current elected master. Peer endpoint lists are only TLS
bootstrap routes for gossip; they are not a permanent leader designation.

Each cluster is limited to 100 nodes. A deployment can use multiple clusters,
but a node identity must be enrolled separately in every cluster it joins.

The normal lifecycle is:

```text
verify release → install → discover capabilities → enroll identity →
connect over TLS → signed capability report → policy admission → lease →
resumable artifact transfer → SHA-256 verify → bounded execution →
checkpoint/yield or result → contribution record → signed state gossip
```

Workers are disabled by default. Installation alone never starts compute.

## 2. Operator prerequisites

Prepare these before touching a production host:

1. A release tree and a trusted Ed25519-signed release manifest.
2. A private CA certificate. Linux workers require `ZDX_CA_FILE`; Android
   accepts the public CA PEM in its mesh settings.
3. A server certificate and private key for every ZDX server endpoint. Keep
   private keys outside the repository and never print them.
4. Ed25519 public keys for every enrolled node, including worker and Android
   identities. Enrollment is an operator action; a presented key is not
   trusted automatically.
5. Firewall rules that expose only the private ZDX TLS port to the node network.
   The installer does not alter SSH, firewalls, existing API ports, or VM
   isolation.
6. A unique node ID and a cluster ID. Keep clocks synchronized because signed
   envelopes have a bounded timestamp window.

## 3. Release verification

From the release root:

```sh
cd /path/to/zdx-pyxel
sha256sum -c release/SHA256SUMS
python3 packaging/release_manifest.py verify release-manifest.json
```

The verifier checks the Ed25519 signature and every manifest file hash. Trust
the release public key through your normal out-of-band process; a signature
made by an untrusted key is not sufficient.

To create a release manifest on a release host without exposing the private
key in output:

```sh
python3 packaging/release_manifest.py create \
  --version 1.0.0 \
  --signing-key /secure/release-ed25519.pem \
  --output release-manifest.json \
  --file packaging/install.sh \
  --file packaging/zdx-server \
  --file packaging/zdxctl \
  --file packaging/systemd/zdx-worker.service \
  --file README.md
```

## 4. Linux/VPS server node

The server launcher is installed by the same installer as the worker. It
starts one server process per enrolled VPS and enables automatic signed gossip.
The following command is the exact bootstrap shape; use your real certificate,
key, CA, and enrolled public-key paths:

```sh
sudo /usr/local/bin/zdx-server \
  --node-id vps-01 \
  --cluster-id private-prod \
  --host 0.0.0.0 --port 8765 \
  --cert-file /etc/zdx-worker/server.crt \
  --key-file /etc/zdx-worker/server.key \
  --ca-file /etc/zdx-worker/ca.pem \
  --trusted-peer vps-02=/etc/zdx-worker/peers/vps-02.pub.pem \
  --trusted-peer android-01=/etc/zdx-worker/peers/android-01.pub.pem \
  --gossip-peer vps-02.example:8765
```

Use one `--trusted-peer` for every node allowed to send authenticated work or
gossip. Use one `--gossip-peer` for each known server bootstrap endpoint. A
temporary partition is tolerated; when connectivity returns, operation hashes
are exchanged, duplicate operations are ignored, and the deterministic
timestamp/operation-ID order resolves concurrent queue, lease, result, and
contribution changes. No manual merge or master-recovery command is required.

For long-running production service management, place the command in a
separately reviewed systemd unit with `User=zdx-worker`, `NoNewPrivileges=true`,
`ProtectSystem=strict`, and only the state/certificate paths in
`ReadWritePaths`. Do not put private keys in unit arguments or logs.

## 5. Install a Linux worker

Run this on each VPS or later laptop. The CA path is intentionally mandatory:

```sh
sudo env \
  ZDX_CA_FILE=/etc/zdx-worker/ca.pem \
  ZDX_COORDINATOR_HOST=vps-01.example \
  ZDX_COORDINATOR_PORT=8765 \
  ZDX_NODE_ID=vps-worker-02 \
  ./packaging/install.sh
```

The installer creates:

| Path | Purpose |
| --- | --- |
| `/opt/zdx` | Runtime modules, including server/worker/artifact code |
| `/usr/local/bin/zdx-server` | Election-aware TLS server launcher |
| `/usr/local/bin/zdxctl` | Status, policy, logs, update, uninstall |
| `/var/lib/zdx-worker` | Private state, identity keys, checkpoints, artifacts |
| `/var/lib/zdx-worker/capability_profile.json` | Hardware-derived profile |
| `/var/lib/zdx-worker/resource_policy.json` | Persistent admission policy |
| `/etc/zdx-worker/worker.env` | Node, endpoint, and CA configuration, mode 0600 |
| `/etc/systemd/system/zdx-worker.service` | Persistent worker service |
| `/etc/systemd/system/zdx-worker.service.d/resources.conf` | Generated cgroup limits |

The service is enabled at boot but compute remains disabled. Inspect first:

```sh
sudo zdxctl status
sudo zdxctl capabilities
sudo zdxctl policy show
sudo zdxctl logs 100
```

Capability discovery derives CPU count, architecture, total/available RAM,
VM features, safe memory, minimum free memory, and recommended concurrency.
Initial Linux limits are conservative: at most 25% of total RAM (capped at
4096 MB), at least 20% of total RAM and 512 MB held free, and at most half the
CPUs capped at four concurrent tasks. Re-running `zdxctl capabilities` refreshes
the profile without enabling compute.

## 6. Linux policy and hard enforcement

Enable only after enrollment and a production review:

```sh
sudo zdxctl policy enable
sudo zdxctl start
sudo zdxctl status
```

Configure stricter limits when needed:

```sh
sudo zdxctl policy set \
  --idle-only true \
  --idle-cpu-percent 15 \
  --memory-mb 1024 \
  --min-free-memory-mb 4096 \
  --max-concurrent-tasks 1 \
  --require-charging false \
  --guard-file /var/lib/zdx-worker/production_busy
```

The service drop-in enforces the generated profile with systemd/cgroup
`CPUQuota`, `MemoryHigh`, `MemoryMax`, `TasksMax`, `CPUWeight=1`, and
`IOWeight=1`. The service also uses `Nice=10`, idle I/O scheduling, restricted
filesystem access, `OOMPolicy=stop`, and `KillMode=mixed`. Python admission is
checked again before every lease and the task executor verifies hashes and
timeouts. If the production guard appears, the active worker watchdog raises a
preemption signal, returns the lease when possible, and leaves the task
recoverable. If cgroup memory is exceeded, systemd stops the worker rather than
allowing it to compete with host workloads; the lease then expires and is
requeued by the replicated state.

Pause immediately for an application deployment:

```sh
sudo touch /var/lib/zdx-worker/production_busy
sudo zdxctl stop
sudo zdxctl logs 200
sudo rm -f /var/lib/zdx-worker/production_busy
sudo zdxctl start
```

## 7. Submit and process a task

From an enrolled operator identity:

```sh
python3 /opt/zdx/zdx_worker.py submit ./program.pyx \
  --coordinator-host vps-01.example --coordinator-port 8765 \
  --node-id operator-01 --key-path /secure/operator-keys \
  --ca-file /etc/zdx-worker/ca.pem \
  --memory-mb 512 --threads 2 --max-seconds 300 \
  --metadata '{"workload_id":"demo-001","task_class":"pyxel-frame"}'
```

The submission uses the artifact digest as the task reference. Workers cannot
read arbitrary submitted filesystem paths. A worker claims a short lease only
after local policy admission, receives a task/peer-scoped grant, downloads in
chunks, checkpoints `.part` state, verifies final SHA-256, executes through the
existing Pyxel VM isolation, returns the result, records contribution fields,
and removes its temporary plaintext execution file.

Artifact behavior is fail-closed:

- every chunk has a SHA-256 receipt and transfers are bounded;
- a disconnect keeps the private checkpoint and resumes from its offset;
- final digest/size mismatch discards the partial file;
- task/peer ownership is checked on every read;
- an expired grant/token cannot be reused; a reconnect must obtain a fresh
  grant for the still-valid task binding;
- encrypted content-addressed blobs expose no general filesystem path.

## 8. Automatic replicated state and failover

Every server periodically pushes and pulls the durable operation snapshot over
the authenticated TLS peer channel. The state includes task submission, lease
claim/release, completion, failure, and contribution records. State gossip has
persistent per-peer sequence counters, so reconnecting does not replay sequence
one. The replicated operation hash makes merges idempotent.

After a partition, both sides may accept independent operations. On rejoin,
the union of valid operations is materialized in deterministic order. A task
with competing claims is resolved by the ordered first valid claim; later
claims cannot resurrect it. Results/failures and releases remove stale queue or
running entries. Karma election is recalculated from the existing election
system; the operator does not manually select a new master.

Contribution records include node, workload, task ID/class, CPU time, RAM time,
verification result, completed work, error, and karma impact. This release does
not implement payments.

## 9. Android installation and use

The Android project builds a signed APK but does not contain a release
keystore, private key, Gradle wrapper, or prebuilt APK. On a trusted Android
build host with SDK 35 and Gradle 8.7+:

```sh
cd android
export ZDX_ANDROID_KEYSTORE=/secure/zdx-release.jks
export ZDX_ANDROID_KEYSTORE_PASSWORD='read-from-secret-store'
export ZDX_ANDROID_KEY_ALIAS=zdx
export ZDX_ANDROID_KEY_PASSWORD='read-from-secret-store'
./gradlew clean assembleRelease
adb install -r app/build/outputs/apk/release/app-release.apk
adb shell am start -n com.zerodrivex.zdxnode/.MainActivity
```

Never commit those variables or the keystore. To update, install the newer
APK with the same application signing identity:

```sh
adb install -r app/build/outputs/apk/release/app-release.apk
```

To cleanly uninstall:

```sh
adb shell am force-stop com.zerodrivex.zdxnode
adb uninstall com.zerodrivex.zdxnode
```

In **ZDX Node**:

1. Review the stable Keystore-backed node identity and detected CPU/RAM.
2. Paste the private CA certificate PEM, enter the enrolled server host/port,
   and enable **Join configured ZDX mesh**.
3. Leave **Enable compute**, **Only run while device is idle**, and **Require
   charging** at their safe defaults until enrollment is complete.
4. Grant notification permission, save settings, then start the foreground
   service.
5. Confirm the notification changes from `starting` to `connected` or an
   explicit policy/mesh error. Stop the service before uninstalling.

The service signs identity, capability registration, polling, lease release,
failure, and result messages with an Android Keystore Ed25519 key. It supports
private TLS CA validation, task-scoped chunked artifact reception, checkpoint
resume after a socket disconnect, final SHA-256 verification, and immediate
yield when charging/idle/free-memory admission changes. It stores no private
key in app logs or general storage.

This source release intentionally fails closed for ordinary Pyxel tasks because
the APK does not embed the Python/Pyxel VM execution adapter. It can execute
only an explicitly marked bounded artifact-integrity probe and return its
verified result. A production Android Pyxel execution adapter, APK build,
device install, and runtime test are true release gates, not silently claimed
as complete by this source tree.

## 10. Update, rollback, and uninstall

Linux updates stop the worker, replace runtime files, preserve identity and
state, regenerate cgroup limits from the existing profile, and reload systemd:

```sh
sudo zdxctl update /path/to/new-release/packaging/install.sh
sudo zdxctl status
```

If rollback is needed, run the installer from the previous verified release
tree. Uninstall retains state for audit:

```sh
sudo zdxctl uninstall
sudo ls -la /var/lib/zdx-worker
```

Review and securely remove state only under your organization’s retention
policy; it may contain encrypted blobs, identity keys, queue recovery data, and
contribution history.

## 11. Troubleshooting

| Symptom | Action |
| --- | --- |
| `worker disabled` | `sudo zdxctl policy show`; enable deliberately |
| CA/TLS failure | Check CA path, server name/certificate, time sync, and firewall |
| identity rejected | Verify node public-key enrollment and persistent key directory |
| replay/sequence rejection | Do not delete identity state; restore the node’s persisted sequence state |
| no task | Check idle/charging/free-memory/guard conditions and task RAM/CPU size |
| task released | Expected when device/host priority changes; the lease is recoverable |
| stale running task after outage | Wait for lease expiry; peers gossip the release/requeue state |
| artifact mismatch | The transfer is discarded; inspect digest and release compatibility |
| Android not connected | Check CA PEM, mesh enabled, notification permission, battery optimization, and server enrollment |
| Android rejects Pyxel task | Expected until an Android VM adapter is built; use Linux workers for Pyxel execution |

Never solve a production issue by disabling TLS, accepting unknown public keys,
removing the production guard, exposing arbitrary filesystem paths, or raising
resource limits without a host-capacity review.
