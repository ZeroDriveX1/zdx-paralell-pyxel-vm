# Coordinator recovery and automatic mesh setup

Android 1.1.1 discovers the cluster through
`https://zerodrivex.com/.well-known/zdx/cluster.json`. The signed APK fixes this
bootstrap origin; Android verifies its public HTTPS certificate and hostname.
No private key, APK signing credential, private CA, or shared enrollment token
is included in the app. Leave **Automatic ZDX mesh setup** enabled, enable mesh
participation, save, and start the background service. Device enrollment and
local compute admission remain separate. This update preserves an existing
installation signed with the replacement 2026 APK key.

The directory accepts only fresh Ed25519-signed registrations from the
operator-owned `/var/lib/zdx-pyxel/coordinators.json` allowlist. Each key is bound
to its exact hostname, port, path, and cluster. Announcements expire in five
minutes and refresh every minute. Removing a coordinator from the allowlist
immediately removes it from new directory responses. Replays cannot extend a
lease. Android fetches the directory each connection cycle and rotates through
live endpoints, rejecting expired manifests and leases. It does not use stale
cached endpoints after discovery fails. Correct device/server clocks are required.

## Replacement host

The gateway listens only on `127.0.0.1:8766` behind the existing HTTPS server.
`packaging/nginx/zdx-mesh.conf` adds three exact routes, leaving application
routes and existing certificates intact. The mesh route uses the HTTP/1.1
`zdx-mesh/1` Upgrade protocol, then carries the unchanged length-prefixed,
signed ZDX messages. It is not WebSocket. No extra inbound firewall port is
required. TLS termination is trusted on the same host; a remote gateway needs
an independently authenticated private transport.

Install requirements.txt and requirements-security.txt into a dedicated venv
under `/opt/zdx-pyxel/venv`; place the release source in
`/opt/zdx-pyxel/runtime`. Create the unprivileged `zdx-pyxel` service account and
install `packaging/systemd/zdx-coordinator.service`. The service owns only
`/var/lib/zdx-pyxel`, has no capabilities, restarts on failure, and is capped at
512 MiB and half one CPU. The process coordinates work; it does not execute
incoming PNGs itself. Keep source and venv root-owned and non-writable by the
service. Validate nginx before reload and retain its previous configuration
for rollback. Test `/healthz` on loopback and the public discovery URL.

First startup creates an Ed25519 server identity, initializes its own approved
endpoint, and automatically publishes its lease. Existing identities are reused.
An empty or invalid existing allowlist fails closed; it is not silently replaced.

## Enroll another coordinator

1. Create its persistent node identity and provision a hostname with a valid
   public HTTPS certificate and the same gateway routes.
2. Independently approve that coordinator's public key and exact endpoint in
   the primary directory's `coordinators.json`. Also enroll coordinator keys
   in each server's `peers.json` for authenticated cluster messages.
3. Run the gateway with the same `--cluster-id`, its unique `--node-id`, its
   `--public-host`, and
   `--registry-url https://zerodrivex.com/zdx/registry/register`.
   Its startup/periodic signed registrations are automatic thereafter.

Discovery and registration do not replicate workloads, identities, or artifacts.
This replacement deploys one coordinator. Additional coordinators must have
verified gossip, consistent enrollment, artifact availability, and fencing before
accepting production failover; endpoint rotation alone is not high availability.
The HTTPS directory is currently a single bootstrap service. Do not claim total
host-loss recovery or automatic replacement provisioning without an independent
backup/replica. Public HTTPS certificate renewal remains nginx/Certbot's existing
responsibility. A compromised domain/TLS termination is a compromised bootstrap
trust anchor; secure its DNS and account access accordingly.

## Enroll a phone

Unknown devices can submit only a self-signed identity request. They receive
`enrollment_pending` and cannot register capabilities or obtain work. Requests
are bounded at 256 pending identities. Inspect `pending_enrollments.json`, verify
the public-key fingerprint against the phone's displayed enrollment fingerprint,
and atomically add the approved `node_id: public_key_pem` entry to `peers.json`.
Keys are reloaded before authentication; no service restart is required. Never
approve solely because a request claims a familiar UUID. A signed APK does not
prove a presented node key belongs to the owner.

Only explicitly approved coordinator keys may exchange cluster state. Work and
artifact submission additionally require membership in `submitters.json` (an
array of approved peer IDs); a worker enrollment does not grant submission rights.
Keep keys, state, enrollment, and role files private, and back them up outside the
host. Revoking a peer by removing it from `peers.json` rejects its next message.
Previously accepted work is not retroactively undone.

## Validation

Python tests cover signature/endpoint/cluster binding, replay, restart durability,
expiry, revocation, bounded request admission, and binary upgrade framing.
Android JVM tests reject stale/wrong-cluster/injected directory entries and
verify the discovered HTTPS configuration. Existing VM parity and isolation
checks remain required. A real phone roundtrip, background lifecycle, and
coordinator failure testing still require device validation. Never test malicious
executable PNGs against the production coordinator or phone.
