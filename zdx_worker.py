"""Capability-reporting worker with resumable artifact downloads."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

from zdx_capabilities import detect_capabilities
from zdx_network import ZDXMessage
from zdx_worker_core import ZDXComputeWorker as _BaseWorker
import zdx_worker_core as _core


class ZDXComputeWorker(_BaseWorker):
    def connect(self) -> None:
        if self.sock is not None:
            return
        sock = self.node.connect()
        profile = detect_capabilities(self.node.node_id)
        capability_reply = self.node.request(sock, ZDXMessage(kind="capability_report", payload=profile.to_payload()))
        if capability_reply.kind != "capability_ack":
            sock.close()
            raise RuntimeError(f"coordinator rejected capability profile: {capability_reply.payload}")
        policy = self.policy_store.load()
        snapshot = _core.current_resource_snapshot()
        reply = self.node.request(sock, ZDXMessage(kind="compute_register", payload={
            "capabilities": {
                **profile.to_payload(), "cpu_count": snapshot.cpu_count,
                "memory_mb": snapshot.total_memory_mb, "available_memory_mb": snapshot.available_memory_mb,
                "safe_limits": {"memory_limit_mb": profile.recommended_memory_mb,
                                 "min_free_memory_mb": profile.recommended_min_free_memory_mb,
                                 "max_concurrent_tasks": profile.recommended_max_concurrent_tasks},
                "idle_only": policy.idle_only,
            }
        }))
        if reply.kind != "compute_ack":
            sock.close()
            raise RuntimeError(f"coordinator rejected worker: {reply.payload}")
        self.sock = sock

    def _materialize_artifact(self, task, token: str) -> str:
        downloads = Path(".zdx/downloads")
        downloads.mkdir(parents=True, exist_ok=True)
        part = downloads / f"{task.task_id}.part"
        metadata_path = downloads / f"{task.task_id}.json"
        expected_digest = task.artifact_digest
        offset = 0
        if metadata_path.exists() and part.exists():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                if metadata.get("digest") == expected_digest and int(metadata.get("size", 0)) == part.stat().st_size:
                    offset = part.stat().st_size
                else:
                    part.unlink(missing_ok=True); metadata_path.unlink(missing_ok=True)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                part.unlink(missing_ok=True); metadata_path.unlink(missing_ok=True)
        while True:
            try:
                manifest = self.node.request(self.sock, ZDXMessage(kind="artifact_download_begin", payload={
                    "task_id": task.task_id, "digest": expected_digest,
                }))
                if manifest.kind != "artifact_download_ready":
                    raise RuntimeError("coordinator did not open artifact download")
                size = int(manifest.payload["size"])
                if size < 1 or size > 8 * 1024 * 1024:
                    raise ValueError("artifact size is outside worker limits")
                if offset > size:
                    part.unlink(missing_ok=True); metadata_path.unlink(missing_ok=True); offset = 0
                metadata_path.write_text(json.dumps({"digest": expected_digest, "size": size}) + "\n", encoding="utf-8")
                with part.open("ab") as stream:
                    while offset < size:
                        response = self.node.request(self.sock, ZDXMessage(kind="artifact_download_chunk", payload={
                            "task_id": task.task_id, "digest": expected_digest,
                            "token": manifest.payload["token"], "offset": offset,
                        }))
                        if response.kind != "artifact_download_chunk":
                            raise RuntimeError("invalid artifact download chunk response")
                        data = base64.b64decode(response.payload["data_b64"].encode("ascii"), validate=True)
                        if not data or int(response.payload["offset"]) != offset or offset + len(data) > size:
                            raise ValueError("invalid artifact download offset or size")
                        stream.write(data); stream.flush(); os.fsync(stream.fileno()); offset += len(data)
                data = part.read_bytes()
                if len(data) != size or hashlib.sha256(data).hexdigest() != expected_digest:
                    part.unlink(missing_ok=True); metadata_path.unlink(missing_ok=True)
                    raise ValueError("artifact final digest mismatch; download discarded")
                metadata_path.unlink(missing_ok=True)
                return str(part)
            except (ConnectionError, OSError):
                # Preserve the private .part checkpoint and reconnect with a fresh grant.
                self.close()
                self.connect()
                offset = part.stat().st_size if part.exists() else 0


_core.ZDXComputeWorker = ZDXComputeWorker
main = _core.main


if __name__ == "__main__":
    raise SystemExit(main())
