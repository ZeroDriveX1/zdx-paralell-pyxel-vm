# ZDX Pyxel networking and cluster operation

## Transport

The canonical node package is `zdx_node/`; the old root-level module is not a
runtime entrypoint. `zdx_network.py` provides length-prefixed JSON envelopes,
bounded messages, request/response framing, timestamps, peer IDs, and sequence
numbers. Production deployments wrap the socket in TLS from `zdx_tls.py` and
verify the complete envelope with Ed25519 before enrollment, replay, rate-limit,
or state operations.

Every private node must be explicitly enrolled by public key. Private keys are
persisted locally and never included in logs, manifests, artifact payloads, or
service arguments.

## Cluster state and election

`zdx_server.py` composes the chunked artifact server, automatic gossip server,
and existing compute server. `zdx_gossip.py` periodically pushes and pulls the
durable operation snapshot from configured peer bootstrap endpoints. Persistent
per-peer sequence counters prevent reconnects from replaying sequence one.

Replicated operations cover:

- task submission and deterministic queue ordering;
- lease claim, release, and expiry/requeue;
- completion, failure, and verification result;
- CPU-time, RAM-time, task class, completed work, and karma-impact records.

Operation hashes make merge idempotent. Partition/rejoin takes the union of
valid operations and materializes them by `(timestamp, operation_id)`. A later
competing claim cannot resurrect a task already resolved by an earlier claim.
Master election remains the existing karmic/gravitational calculation, with a
maximum of 100 members per cluster and no fixed coordinator.

## Artifact mobility

`zdx_artifacts.py` stores immutable plaintext-addressed blobs encrypted with
AES-GCM at rest. The network sees only a task-scoped grant and digest. The
chunked server protocol supports upload begin/chunk/finish and download
begin/chunk. Every chunk is bounded and hashed; transfer metadata and partial
data are durable. A final digest/size mismatch discards the transfer. Grants
are bound to task, digest, and peer and cannot be reused after expiry.

## Compute messages

Authenticated workers use:

- `capability_report` — signed hardware and safe-limit profile;
- `compute_register` — register a worker with current capability policy;
- `compute_submit` — submit a content-addressed task;
- `compute_poll` — request work using current available memory/CPU;
- `artifact_download_begin/chunk` — receive a scoped resumable artifact;
- `compute_result`, `compute_fail`, `compute_release` — close or yield a lease.

The worker checks local policy immediately before polling and again after a
claim. Linux systemd/cgroup limits are an independent hard boundary. Network
transport never grants arbitrary host filesystem access or arbitrary remote
Python/shell execution.

## Android transport

The Android service uses the same envelope shape, app-private checkpoints,
private-CA validation, and Keystore Ed25519 identity. It registers dynamic
CPU/RAM/charging/GPU/NPU capability facts, polls only under persisted idle/
charging/free-memory policy, resumes chunk downloads, verifies SHA-256, and
returns bounded probe results. Ordinary Pyxel execution is rejected until a
signed Android VM adapter is supplied.
