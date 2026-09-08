"""Gossip-aware ZDX server with resumable artifact protocol messages."""

from __future__ import annotations

import base64

from zdx_artifacts import MAX_CHUNK_BYTES
from zdx_network import ZDXMessage
import zdx_server_gossip_core as _core


class ZDXServer(_core.ZDXServer):
    _COMPUTE_KINDS = _core.ZDXServer._COMPUTE_KINDS | {
        "artifact_upload_begin", "artifact_upload_chunk", "artifact_upload_finish",
        "artifact_download_begin", "artifact_download_chunk", "artifact_transfer_cleanup",
    }

    def _handle_compute(self, conn, message: ZDXMessage) -> None:
        payload = message.payload
        if message.kind == "artifact_upload_begin":
            manifest = self.artifacts.begin_upload(
                str(payload["task_id"]), str(payload["digest"]).lower(), int(payload["size"]),
                message.peer_id, int(payload.get("ttl_seconds", 900)),
            )
            self._send(conn, ZDXMessage(kind="artifact_upload_ready", payload=manifest))
            return
        if message.kind == "artifact_upload_chunk":
            encoded = payload.get("data_b64", "")
            data = base64.b64decode(encoded.encode("ascii"), validate=True)
            if len(data) > MAX_CHUNK_BYTES:
                raise ValueError("artifact chunk exceeds maximum size")
            receipt = self.artifacts.put_chunk(
                str(payload["transfer_id"]), message.peer_id, int(payload["offset"]),
                data, str(payload["sha256"]),
            )
            self._send(conn, ZDXMessage(kind="artifact_upload_receipt", payload=receipt))
            return
        if message.kind == "artifact_upload_finish":
            ref = self.artifacts.finish_upload(str(payload["transfer_id"]), message.peer_id)
            self._send(conn, ZDXMessage(kind="artifact_upload_complete", payload=ref.to_dict()))
            return
        if message.kind == "artifact_download_begin":
            manifest = self.artifacts.begin_download(
                str(payload["task_id"]), str(payload["digest"]).lower(), message.peer_id,
                int(payload.get("ttl_seconds", 900)),
            )
            self._send(conn, ZDXMessage(kind="artifact_download_ready", payload=manifest))
            return
        if message.kind == "artifact_download_chunk":
            chunk = self.artifacts.read_chunk(
                str(payload["task_id"]), str(payload["digest"]).lower(), message.peer_id,
                str(payload["token"]), int(payload["offset"]),
            )
            chunk["data_b64"] = base64.b64encode(chunk.pop("data")).decode("ascii")
            self._send(conn, ZDXMessage(kind="artifact_download_chunk", payload=chunk))
            return
        if message.kind == "artifact_transfer_cleanup":
            removed = self.artifacts.cleanup_expired()
            self._send(conn, ZDXMessage(kind="artifact_transfer_ack", payload={"removed": removed}))
            return
        super()._handle_compute(conn, message)
