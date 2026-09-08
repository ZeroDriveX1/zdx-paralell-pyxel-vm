"""Final production server composition with Android result verification."""

from __future__ import annotations

import hashlib
import json

import zdx_server_chunked as _chunked
from zdx_ed25519_signer import verify_ed25519_signature


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class ZDXServer(_chunked.ZDXServer):
    """Chunked/gossip server with complete replicated recovery and attestations."""

    def _recover_from_cluster_state(self) -> None:
        super()._recover_from_cluster_state()
        materialized = self.cluster_state.materialized()
        self.compute._state["contributions"] = materialized["contributions"]
        self.compute._save()

    def _verify_android_result(self, peer_id: str, task_id: str, result: dict) -> None:
        signature = result.get("attestation_signature")
        if not signature:
            return
        public_key = result.get("public_key")
        if not public_key or public_key != self._peer_public_keys.get(peer_id):
            raise PermissionError("result attestation key is not enrolled")
        signed_claim = {
            key: result[key]
            for key in ("attestation_version", "node_id", "result", "result_sha256")
            if key in result
        }
        if signed_claim.get("node_id") != peer_id or signed_claim.get("attestation_version") != 1:
            raise PermissionError("result attestation identity or version is invalid")
        if hashlib.sha256(_canonical(signed_claim.get("result", {})).encode("utf-8")).hexdigest() != signed_claim.get("result_sha256"):
            raise PermissionError("result attestation digest is invalid")
        if not verify_ed25519_signature(public_key, signed_claim, signature):
            raise PermissionError("result attestation signature is invalid")
        inner = signed_claim.get("result", {})
        if inner.get("task_id") != task_id or not inner.get("adapter_id") or not inner.get("adapter_manifest_sha256"):
            raise PermissionError("result attestation task or adapter identity is missing")

    def _record_compute_deltas(self, before: dict, after: dict, origin: str) -> None:
        super()._record_compute_deltas(before, after, origin)
        materialized = self.cluster_state.materialized()
        before_running = before.get("running", {})
        after_queued = after.get("queued", {})
        for task_id, record in before_running.items():
            if task_id in after_queued and task_id in materialized["running"]:
                self.cluster_state.release_task(
                    task_id,
                    str(record.get("lease_id", "")),
                    str(after_queued[task_id].get("metadata", {}).get("last_release_reason", "lease expired or released")),
                    origin,
                )

    def _handle_compute(self, conn, message):
        if message.kind == "compute_result":
            result = message.payload.get("result", {})
            if isinstance(result, dict) and result.get("attestation_signature"):
                self._verify_android_result(message.peer_id, str(message.payload.get("task_id", "")), result)
        super()._handle_compute(conn, message)


if __name__ == "__main__":
    raise SystemExit("Use the deployment server entrypoint to configure TLS and peer enrollment")
