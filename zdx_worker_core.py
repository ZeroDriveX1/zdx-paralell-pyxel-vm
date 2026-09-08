"""Policy-gated persistent ZDX compute worker."""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import ssl
import tempfile
import threading
from pathlib import Path
from typing import Optional

from zdx_compute import ComputeTask, execute_task, sha256_file
from zdx_ed25519_signer import ZDXEd25519Signer
from zdx_network import ZDXMessage
from zdx_node import ZDXNode
from zdx_resource_policy import ResourcePolicyStore, current_resource_snapshot
from zdx_tls import client_context


class ZDXComputeWorker:
    """A persistent worker that is disabled until its policy is enabled."""

    def __init__(self, coordinator_host: str, coordinator_port: int, *, node_id: str,
                 key_path: str = ".zdx/keys", policy_path: str = ".zdx/resource_policy.json",
                 protected_processes: tuple[str, ...] = (), tls_context: Optional[ssl.SSLContext] = None):
        self.policy_store = ResourcePolicyStore(policy_path)
        self.signer = ZDXEd25519Signer(node_id=node_id, key_path=key_path)
        self.node = ZDXNode(host=coordinator_host, port=coordinator_port, node_id=node_id,
                            signer=self.signer, tls_context=tls_context)
        self.protected_processes = protected_processes
        self.sock: Optional[socket.socket] = None
        self.stop_event = threading.Event()
        self._nice_applied = False

    def connect(self) -> None:
        if self.sock is not None:
            return
        sock = self.node.connect()
        policy = self.policy_store.load()
        snapshot = current_resource_snapshot()
        reply = self.node.request(sock, ZDXMessage(kind="compute_register", payload={
            "capabilities": {
                "cpu_count": snapshot.cpu_count, "memory_mb": snapshot.total_memory_mb,
                "available_memory_mb": snapshot.available_memory_mb,
                "vm_features": ["pyxel-vm", "verified-frame-execution"], "idle_only": policy.idle_only,
            }
        }))
        if reply.kind != "compute_ack":
            sock.close()
            raise RuntimeError(f"coordinator rejected worker: {reply.payload}")
        self.sock = sock

    def close(self) -> None:
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def _materialize_artifact(self, task: ComputeTask, token: str) -> str:
        response = self.node.request(self.sock, ZDXMessage(kind="artifact_get", payload={
            "task_id": task.task_id, "digest": task.artifact_digest, "token": token,
        }))
        if response.kind != "artifact_data":
            raise RuntimeError("coordinator did not return the requested artifact")
        if response.payload.get("digest") != task.artifact_digest:
            raise ValueError("artifact response digest mismatch")
        try:
            data = base64.b64decode(response.payload["data_b64"].encode("ascii"), validate=True)
        except (KeyError, ValueError, UnicodeError) as exc:
            raise ValueError("artifact response is malformed") from exc
        if len(data) > 8 * 1024 * 1024:
            raise ValueError("artifact exceeds worker limit")
        with tempfile.NamedTemporaryFile(prefix=f"zdx-{task.task_id}-", suffix=".pyx", dir=".zdx", delete=False) as stream:
            os.chmod(stream.name, 0o600)
            stream.write(data)
            path = stream.name
        if sha256_file(path) != task.artifact_digest:
            os.unlink(path)
            raise ValueError("artifact integrity check failed")
        return path

    def run_once(self) -> dict:
        policy = self.policy_store.load()
        snapshot = current_resource_snapshot(self.protected_processes)
        allowed, reason = policy.admit(snapshot, protected_processes=self.protected_processes)
        if not allowed:
            return {"status": "paused", "reason": reason}
        if not self._nice_applied and policy.nice_level:
            try:
                os.nice(policy.nice_level)
            except (AttributeError, OSError):
                pass
            self._nice_applied = True
        self.connect()
        response = self.node.request(self.sock, ZDXMessage(kind="compute_poll", payload={
            "available_memory_mb": policy.available_budget_mb(snapshot),
            "cpu_count": snapshot.cpu_count, "cpu_percent": snapshot.cpu_percent,
        }))
        if response.kind != "compute_task":
            raise RuntimeError(f"unexpected coordinator response: {response.kind}")
        raw_task = response.payload.get("task")
        if raw_task is None:
            return {"status": "idle", "reason": "no task available"}
        task = ComputeTask.from_dict(raw_task)
        allowed, reason = policy.admit(snapshot, task.memory_mb, self.protected_processes)
        if not allowed:
            self.node.request(self.sock, ZDXMessage(kind="compute_release", payload={"task_id": task.task_id, "reason": reason}))
            return {"status": "released", "task_id": task.task_id, "reason": reason}
        temporary_path = None
        try:
            if task.artifact_digest:
                temporary_path = self._materialize_artifact(task, str(response.payload.get("artifact_token", "")))
                task.frame_path = temporary_path
            result = execute_task(task)
        except Exception as exc:
            self.node.request(self.sock, ZDXMessage(kind="compute_fail", payload={"task_id": task.task_id, "error": str(exc)}))
            return {"status": "failed", "task_id": task.task_id, "error": str(exc)}
        finally:
            if temporary_path:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass
        self.node.request(self.sock, ZDXMessage(kind="compute_result", payload={"task_id": task.task_id, "result": result}))
        return {"status": "completed", "task_id": task.task_id}

    def run_forever(self) -> None:
        while not self.stop_event.is_set():
            try:
                outcome = self.run_once()
                self.stop_event.wait(self.policy_store.load().poll_interval_seconds if outcome.get("status") == "paused" else 0.1)
            except (ConnectionError, OSError, socket.timeout, RuntimeError) as exc:
                self.close()
                self.stop_event.wait(5.0)
                if not self.stop_event.is_set():
                    print(f"[zdx-worker] coordinator unavailable: {exc}")

    def stop(self) -> None:
        self.stop_event.set()
        self.close()


def _parse_bool(value: str) -> bool:
    lowered = value.lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def _tls_context(args):
    if not args.ca_file:
        return None
    return client_context(cafile=args.ca_file, certfile=args.client_cert, keyfile=args.client_key)


def _policy_command(args) -> None:
    store = ResourcePolicyStore(args.config)
    if args.action == "show":
        print(json.dumps(store.load().__dict__, indent=2, sort_keys=True))
    elif args.action == "enable":
        store.update(enabled=True)
    elif args.action == "disable":
        store.update(enabled=False)
    else:
        changes = {name: value for name, value in {
            "idle_only": args.idle_only, "idle_cpu_percent": args.idle_cpu_percent,
            "memory_limit_mb": args.memory_mb, "min_free_memory_mb": args.min_free_memory_mb,
            "max_concurrent_tasks": args.max_concurrent_tasks, "require_charging": args.require_charging,
            "production_guard_file": args.guard_file, "poll_interval_seconds": args.poll_interval_seconds,
        }.items() if value is not None}
        store.update(**changes)


def _submit_command(args) -> None:
    signer = ZDXEd25519Signer(node_id=args.node_id, key_path=args.key_path)
    node = ZDXNode(host=args.coordinator_host, port=args.coordinator_port, node_id=args.node_id,
                   signer=signer, tls_context=_tls_context(args))
    sock = node.connect()
    try:
        metadata = json.loads(args.metadata) if args.metadata else {}
        with open(args.frame, "rb") as stream:
            data = stream.read()
        digest = sha256_file(args.frame)
        task = ComputeTask(frame_path="", frame_sha256=digest, artifact_digest=digest,
                           memory_mb=args.memory_mb, threads=args.threads,
                           max_seconds=args.max_seconds, metadata=metadata)
        upload = node.request(sock, ZDXMessage(kind="artifact_put", payload={
            "task_id": task.task_id, "digest": digest, "data_b64": base64.b64encode(data).decode("ascii"),
        }))
        if upload.kind != "artifact_ack":
            raise RuntimeError("artifact upload was rejected")
        response = node.request(sock, ZDXMessage(kind="compute_submit", payload={"task": task.to_dict()}))
        print(json.dumps(response.payload, sort_keys=True))
    finally:
        sock.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="ZDX persistent distributed compute worker")
    sub = parser.add_subparsers(dest="command", required=True)
    policy = sub.add_parser("policy", help="view or change persistent resource policy")
    policy.add_argument("action", choices=("show", "enable", "disable", "set")); policy.add_argument("--config", default=".zdx/resource_policy.json")
    policy.add_argument("--idle-only", type=_parse_bool); policy.add_argument("--idle-cpu-percent", type=float); policy.add_argument("--memory-mb", type=int)
    policy.add_argument("--min-free-memory-mb", type=int); policy.add_argument("--max-concurrent-tasks", type=int); policy.add_argument("--require-charging", type=_parse_bool)
    policy.add_argument("--guard-file"); policy.add_argument("--poll-interval-seconds", type=float)
    run = sub.add_parser("run", help="run the persistent background worker")
    run.add_argument("--coordinator-host", default="127.0.0.1"); run.add_argument("--coordinator-port", type=int, default=8765); run.add_argument("--node-id", required=True)
    run.add_argument("--key-path", default=".zdx/keys"); run.add_argument("--config", default=".zdx/resource_policy.json"); run.add_argument("--protected-process", action="append", default=[]); run.add_argument("--once", action="store_true")
    run.add_argument("--ca-file"); run.add_argument("--client-cert"); run.add_argument("--client-key")
    submit = sub.add_parser("submit", help="upload and submit a verified frame task")
    submit.add_argument("frame"); submit.add_argument("--coordinator-host", default="127.0.0.1"); submit.add_argument("--coordinator-port", type=int, default=8765); submit.add_argument("--node-id", required=True); submit.add_argument("--key-path", default=".zdx/keys")
    submit.add_argument("--memory-mb", type=int, default=256); submit.add_argument("--threads", type=int, default=1); submit.add_argument("--max-seconds", type=int, default=300); submit.add_argument("--metadata", help="JSON object attached to the task"); submit.add_argument("--ca-file"); submit.add_argument("--client-cert"); submit.add_argument("--client-key")
    args = parser.parse_args(argv)
    if args.command == "policy":
        _policy_command(args); return 0
    if args.command == "submit":
        _submit_command(args); return 0
    worker = ZDXComputeWorker(args.coordinator_host, args.coordinator_port, node_id=args.node_id, key_path=args.key_path, policy_path=args.config, protected_processes=tuple(args.protected_process), tls_context=_tls_context(args))
    try:
        if args.once:
            print(json.dumps(worker.run_once(), sort_keys=True))
        else:
            worker.run_forever()
    finally:
        worker.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
