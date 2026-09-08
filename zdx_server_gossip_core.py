"""ZDX server entrypoint with automatic state gossip and chunk transfers."""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import ssl
import threading
from typing import Iterable, Mapping, Optional

from zdx_auth_pipeline import AuthenticationError
from zdx_ed25519_signer import verify_ed25519_signature
from zdx_cluster import KarmicClusterRuntime, ReplicatedClusterState
from zdx_gossip import StateGossipAgent
from zdx_network import ZDXMessage, recv_message, heartbeat
import zdx_server_core as _core


class ZDXServer(_core.ZDXServer):
    """Existing server plus automatic mergeable-state gossip."""

    _CLUSTER_KINDS = {"cluster_state_push", "cluster_state_pull", "cluster_hello"}

    def __init__(self, *args, node_id: str | None = None, cluster_id: str = "default",
                 karma_path: str = ".zdx/karma.json",
                 cluster_state_path: str = ".zdx/cluster_state.json",
                 gossip_peers: Iterable[tuple[str, int]] = (),
                 gossip_interval_seconds: float = 5.0, **kwargs):
        super().__init__(*args, **kwargs)
        self.cluster = None
        self.cluster_state = ReplicatedClusterState(cluster_state_path, cluster_id)
        if node_id:
            self.cluster = KarmicClusterRuntime(
                node_id, cluster_id, karma_path=karma_path, state_path=cluster_state_path
            )
            self.cluster.join()
        self.gossip_peers = list(gossip_peers)
        self.gossip_interval_seconds = gossip_interval_seconds
        self._gossip_agent: Optional[StateGossipAgent] = None
        self._pending_enrollment_lock = threading.Lock()
        self._recover_from_cluster_state()

    def _recover_from_cluster_state(self) -> None:
        materialized = self.cluster_state.materialized()
        current = self.compute.status()
        current.update({key: materialized[key] for key in ("queued", "running", "completed", "failed")})
        self.compute._state.update({key: current[key] for key in ("queued", "running", "completed", "failed")})
        self.compute._save()

    def _handle_cluster(self, conn, message: ZDXMessage) -> None:
        if message.kind == "cluster_state_push":
            added = self.cluster_state.merge(message.payload["snapshot"])
            self._recover_from_cluster_state()
            self._send(conn, ZDXMessage(kind="cluster_state_ack", payload={"added": added, "master": self.elected_master}))
        elif message.kind == "cluster_state_pull":
            self._send(conn, ZDXMessage(kind="cluster_state_snapshot", payload={"snapshot": self.cluster_state.snapshot()}))
        else:
            self._send(conn, ZDXMessage(kind="cluster_hello", payload={"cluster_id": self.cluster_state.cluster_id, "master": self.elected_master}))

    @property
    def elected_master(self) -> str | None:
        return self.cluster.master_id() if self.cluster is not None else None

    def _record_compute_deltas(self, before: dict, after: dict, origin: str) -> None:
        materialized = self.cluster_state.materialized()
        for task_id, task in after.get("queued", {}).items():
            if task_id not in materialized["queued"] and task_id not in materialized["running"] and task_id not in materialized["completed"] and task_id not in materialized["failed"]:
                self.cluster_state.queue_task(task, origin)
        for task_id, record in after.get("running", {}).items():
            if task_id not in materialized["running"]:
                self.cluster_state.append("lease_claim", {
                    "task_id": task_id, "worker_id": record["worker_id"],
                    "lease_id": record.get("lease_id", ""), "lease_until": record["lease_until"],
                }, origin)
        for task_id, record in after.get("completed", {}).items():
            if task_id not in materialized["completed"]:
                self.cluster_state.append("task_complete", {
                    "task_id": task_id, "lease_id": record.get("lease_id", ""),
                    "task": record.get("task", {}), "result": record.get("result", {}),
                    "verification_result": "verified",
                }, origin)
        for task_id, record in after.get("failed", {}).items():
            if task_id not in materialized["failed"]:
                self.cluster_state.append("task_fail", {
                    "task_id": task_id, "lease_id": record.get("lease_id", ""),
                    "task": record.get("task", {}), "error": record.get("error", ""),
                }, origin)
        for key, record in after.get("contributions", {}).items():
            if key not in materialized["contributions"]:
                self.cluster_state.append("contribution", {"contribution_id": key, **record}, origin)

    def _handle_compute(self, conn, message: ZDXMessage) -> None:
        before = self.compute.status()
        super()._handle_compute(conn, message)
        self._record_compute_deltas(before, self.compute.status(), message.peer_id)

    def _queue_pending_enrollment(self, conn, message: ZDXMessage, address) -> bool:
        if message.kind != "identity" or message.peer_id in self._enrolled_peers:
            return False
        node_id = str(message.payload.get("node_id", ""))
        public_key = message.payload.get("public_key")
        if node_id != message.peer_id or not isinstance(public_key, str) or not public_key:
            raise AuthenticationError(stage="enrollment_request", reason="identity requires matching node_id and public key", peer_id=message.peer_id)
        if len(public_key) > 8192:
            raise AuthenticationError(stage="enrollment_request", reason="public key is too large", peer_id=message.peer_id)
        if not verify_ed25519_signature(public_key, message.auth_payload(), message.signature):
            raise AuthenticationError(stage="signature_verification", reason="pending enrollment self-signature is invalid", peer_id=message.peer_id)
        fingerprint = hashlib.sha256(public_key.encode("utf-8")).hexdigest()
        pending_path = self.state.path.parent / "pending_enrollments.json"
        with self._pending_enrollment_lock:
            try:
                pending = json.loads(pending_path.read_text(encoding="utf-8")) if pending_path.exists() else {}
            except (OSError, json.JSONDecodeError):
                pending = {}
            pending[message.peer_id] = {
                "public_key": public_key,
                "public_key_sha256": fingerprint,
                "address": str(address),
                "requested_at": self.state.now(),
            }
            pending_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = pending_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(pending, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            temporary.replace(pending_path)
        self._send(conn, ZDXMessage(kind="enrollment_pending", payload={
            "accepted": False, "status": "pending", "node_id": message.peer_id,
            "public_key_sha256": fingerprint,
        }))
        return True

    def handle_client(self, conn, address):
        try:
            while self.running:
                message = recv_message(conn)
                if self.require_auth or message.kind in self._COMPUTE_KINDS or message.kind in self._CLUSTER_KINDS:
                    if self.require_auth and self._queue_pending_enrollment(conn, message, address):
                        return
                    self._authenticate(message)
                if message.kind in self._CLUSTER_KINDS:
                    self._handle_cluster(conn, message)
                    continue
                if message.kind in self._COMPUTE_KINDS:
                    self._handle_compute(conn, message)
                    continue
                if message.kind == "identity":
                    if self.require_auth:
                        if message.payload.get("node_id") != message.peer_id:
                            raise AuthenticationError(stage="identity", reason="identity node_id does not match signed peer_id", peer_id=message.peer_id)
                        claimed_key = message.payload.get("public_key")
                        if claimed_key and claimed_key != self._peer_public_keys.get(message.peer_id):
                            raise AuthenticationError(stage="identity", reason="identity public key does not match enrolled key", peer_id=message.peer_id)
                    self.peers[address] = message.payload
                    self.state.record_peer(address, message.payload)
                    self._send(conn, ZDXMessage(kind="identity_ack", payload={"accepted": True}))
                elif message.kind == "capability_report":
                    self.capabilities[address] = message.payload
                    self.state.record_peer(address, {"capabilities": message.payload})
                    self._send(conn, ZDXMessage(kind="capability_ack", payload={"accepted": True}))
                elif message.kind == "heartbeat":
                    self.state.record_heartbeat()
                    self._send(conn, heartbeat())
                else:
                    self._send(conn, ZDXMessage(kind="ack", payload={"received": message.kind}))
        except AuthenticationError as exc:
            try:
                self._send(conn, ZDXMessage(kind="auth_error", payload={"stage": exc.stage, "reason": exc.reason}))
            except OSError:
                pass
        except (ConnectionError, OSError, ValueError, PermissionError, KeyError):
            self.peers.pop(address, None)
            self.capabilities.pop(address, None)
        finally:
            conn.close()

    def serve(self):
        if self.cluster is not None and self.gossip_peers:
            self._gossip_agent = StateGossipAgent(
                self.cluster, self.cluster_state, self.cluster.node_id,
                self.signer, self.gossip_peers, tls_context=self.tls_context,
                interval_seconds=self.gossip_interval_seconds,
            )
            self._gossip_agent.start()
        try:
            super().serve()
        finally:
            if self._gossip_agent is not None:
                self._gossip_agent.stop()
