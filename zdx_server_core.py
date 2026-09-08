"""Authenticated ZDX transport server with private artifact mobility."""

from __future__ import annotations

import base64
import socket
import ssl
import threading
from typing import Mapping, Optional

from zdx_artifacts import ContentAddressedBlobStore, MAX_ARTIFACT_BYTES
from zdx_auth_pipeline import AuthenticationError, ZDXAuthenticationPipeline
from zdx_compute import ComputeCoordinator, ComputeTask
from zdx_ed25519_signer import verify_ed25519_signature
from zdx_network import recv_message, send_message, ZDXMessage, heartbeat
from zdx_state import ZDXState


class ZDXServer:
    _COMPUTE_KINDS = {
        "compute_register", "compute_submit", "compute_poll", "compute_result",
        "compute_release", "compute_fail", "artifact_put", "artifact_get",
    }

    def __init__(
        self, host="0.0.0.0", port=8765, *, require_auth: bool = False,
        trusted_peers: Optional[Mapping[str, str]] = None, signer=None,
        state_path: str = ".zdx/node_state.json",
        compute_state_path: str = ".zdx/compute_state.json",
        artifact_root: str = ".zdx/artifacts",
        tls_context: Optional[ssl.SSLContext] = None,
        allow_shared_paths: bool = False,
    ):
        self.host = host
        self.port = port
        self.running = False
        self.peers = {}
        self.capabilities = {}
        self.state = ZDXState(state_path)
        self.compute = ComputeCoordinator(compute_state_path)
        self.artifacts = ContentAddressedBlobStore(artifact_root)
        self.require_auth = require_auth
        self.signer = signer
        self.tls_context = tls_context
        self.allow_shared_paths = allow_shared_paths
        self._peer_public_keys = dict(trusted_peers or {})
        self._enrolled_peers = set(self._peer_public_keys)
        self._revoked_peers = set()
        self._auth_pipeline = ZDXAuthenticationPipeline(
            verify_signature=self._verify_signature,
            check_revocation=lambda peer_id: peer_id in self._revoked_peers,
            check_enrollment=lambda peer_id: (
                peer_id in self._enrolled_peers,
                {"status": "approved"} if peer_id in self._enrolled_peers else None,
            ),
            dispatch_handler=lambda _peer_id, _payload: None,
        )

    def register_peer(self, peer_id: str, public_key_pem: str) -> None:
        if not peer_id or not public_key_pem:
            raise ValueError("peer_id and public_key_pem are required")
        self._peer_public_keys[peer_id] = public_key_pem
        self._enrolled_peers.add(peer_id)
        self._revoked_peers.discard(peer_id)

    def revoke_peer(self, peer_id: str) -> None:
        self._revoked_peers.add(peer_id)
        self._enrolled_peers.discard(peer_id)
        self._auth_pipeline.clear_replay_state(peer_id)

    def _verify_signature(self, peer_id: str, payload: dict, signature: str) -> bool:
        public_key = self._peer_public_keys.get(peer_id)
        return bool(public_key) and verify_ed25519_signature(public_key, payload, signature)

    def _authenticate(self, message: ZDXMessage) -> None:
        if not message.peer_id or not message.signature:
            raise AuthenticationError(stage="envelope", reason="authenticated message is missing identity or signature", peer_id=message.peer_id)
        self._auth_pipeline.process_message(
            peer_id=message.peer_id, protocol_version=message.version,
            timestamp=message.timestamp, signature=message.signature,
            payload=message.auth_payload(), sequence=message.sequence,
        )

    def _send(self, conn, message: ZDXMessage) -> None:
        if self.signer is not None:
            message.peer_id = self.signer.node_id
            message.sign(self.signer)
        send_message(conn, message)

    @staticmethod
    def _decode_artifact(payload: dict) -> tuple[str, str, bytes]:
        task_id = str(payload.get("task_id", ""))
        digest = str(payload.get("digest", "")).lower()
        encoded = payload.get("data_b64", "")
        if not task_id or len(digest) != 64 or not isinstance(encoded, str):
            raise ValueError("artifact_put requires task_id, digest, and data_b64")
        try:
            data = base64.b64decode(encoded.encode("ascii"), validate=True)
        except (ValueError, UnicodeError) as exc:
            raise ValueError("artifact data is not valid base64") from exc
        if len(data) > MAX_ARTIFACT_BYTES:
            raise ValueError("artifact exceeds maximum size")
        return task_id, digest, data

    def _handle_compute(self, conn, message: ZDXMessage) -> None:
        payload = message.payload
        if message.kind == "artifact_put":
            task_id, digest, data = self._decode_artifact(payload)
            ref = self.artifacts.put_bytes(data, expected_digest=digest)
            self._send(conn, ZDXMessage(kind="artifact_ack", payload={"task_id": task_id, "digest": ref.digest, "size": ref.size}))
            return
        if message.kind == "artifact_get":
            task_id = str(payload.get("task_id", ""))
            digest = str(payload.get("digest", "")).lower()
            data = self.artifacts.read_granted(task_id, digest, message.peer_id, str(payload.get("token", "")))
            self._send(conn, ZDXMessage(kind="artifact_data", payload={
                "task_id": task_id, "digest": digest,
                "data_b64": base64.b64encode(data).decode("ascii"),
            }))
            return
        if message.kind == "compute_register":
            self.compute.register_worker(message.peer_id, payload.get("capabilities", payload))
            self._send(conn, ZDXMessage(kind="compute_ack", payload={"operation": "register", "accepted": True}))
        elif message.kind == "compute_submit":
            raw_task = dict(payload.get("task", payload))
            task = ComputeTask.from_dict(raw_task)
            if task.artifact_digest:
                self.artifacts.bind_task(task.task_id, task.artifact_digest, message.peer_id)
                task.frame_path = ""
            elif not self.allow_shared_paths:
                raise PermissionError("production transport requires a content-addressed artifact")
            self.compute.submit(task)
            self._send(conn, ZDXMessage(kind="compute_ack", payload={"operation": "submit", "task_id": task.task_id}))
        elif message.kind == "compute_poll":
            task = self.compute.claim(message.peer_id, int(payload.get("available_memory_mb", 0)), int(payload.get("cpu_count", 1)))
            response = {"task": task.to_dict() if task else None}
            if task and task.artifact_digest:
                response["artifact_token"] = self.artifacts.grant(task.task_id, task.artifact_digest, message.peer_id)
            self._send(conn, ZDXMessage(kind="compute_task", payload=response))
        elif message.kind == "compute_result":
            self.compute.complete(message.peer_id, str(payload["task_id"]), payload.get("result", {}), lease_id=payload.get("lease_id"))
            self._send(conn, ZDXMessage(kind="compute_ack", payload={"operation": "complete", "accepted": True}))
        elif message.kind == "compute_release":
            self.compute.release(message.peer_id, str(payload["task_id"]), str(payload.get("reason", "resource policy")), lease_id=payload.get("lease_id"))
            self._send(conn, ZDXMessage(kind="compute_ack", payload={"operation": "release", "accepted": True}))
        elif message.kind == "compute_fail":
            self.compute.fail(message.peer_id, str(payload["task_id"]), str(payload.get("error", "worker failure")), lease_id=payload.get("lease_id"))
            self._send(conn, ZDXMessage(kind="compute_ack", payload={"operation": "fail", "accepted": True}))

    def handle_client(self, conn, address):
        try:
            while self.running:
                message = recv_message(conn)
                if self.require_auth or message.kind in self._COMPUTE_KINDS:
                    self._authenticate(message)
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
        self.running = True
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.host, self.port))
            self.port = server.getsockname()[1]
            server.listen()
            server.settimeout(0.25)
            while self.running:
                try:
                    conn, address = server.accept()
                except socket.timeout:
                    continue
                if self.tls_context is not None:
                    try:
                        conn = self.tls_context.wrap_socket(conn, server_side=True)
                    except (OSError, ssl.SSLError):
                        conn.close()
                        continue
                threading.Thread(target=self.handle_client, args=(conn, address), daemon=True).start()

    def stop(self):
        self.running = False
