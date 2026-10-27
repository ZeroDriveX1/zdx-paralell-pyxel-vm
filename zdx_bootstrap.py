"""HTTPS discovery and an HTTP Upgrade gateway for authenticated ZDX streams.

Run behind an HTTPS reverse proxy bound to this loopback-only listener. The
registry authenticates coordinator announcements against an operator-owned
allowlist; HTTPS authenticates the directory to clients. No private CA or
shared registration secret is distributed to phones.
"""
from __future__ import annotations

import argparse
import json
import logging
import socketserver
import threading
import time
import urllib.request
from urllib.parse import urlsplit
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from zdx_ed25519_signer import ZDXEd25519Signer, verify_ed25519_signature
from zdx_server import ZDXServer
from zdx_storage import atomic_write_secret

LOG = logging.getLogger("zdx.bootstrap")
DIRECTORY_PATH = "/.well-known/zdx/cluster.json"
REGISTER_PATH = "/zdx/registry/register"
MESH_PATH = "/zdx/mesh"
MAX_BODY = 32768


class CoordinatorDirectory:
    """Durable signed leases. Revocation takes effect on the next read."""

    def __init__(self, root: Path, cluster_id: str, clock=time.time):
        self.root, self.cluster_id, self.clock = root, cluster_id, clock
        self.lock = threading.RLock()
        root.mkdir(parents=True, exist_ok=True)

    def approved(self):
        value = json.loads((self.root / "coordinators.json").read_text())
        if not isinstance(value, dict) or len(value) > 100:
            raise ValueError("invalid coordinator allowlist")
        return value

    def validate(self, announcement):
        payload = announcement["payload"]
        now = int(self.clock())
        node_id = payload["node_id"]
        approved = self.approved().get(node_id)
        if not approved or not verify_ed25519_signature(
            approved["public_key"], payload, announcement["signature"]
        ):
            raise PermissionError("coordinator is not enrolled or signature is invalid")
        if payload["cluster_id"] != self.cluster_id:
            raise PermissionError("wrong cluster")
        issued, expires = payload["issued_at"], payload["expires_at"]
        if type(issued) is not int or type(expires) is not int:
            raise ValueError("timestamps must be integers")
        if not now - 600 <= issued <= now + 30 or not now < expires <= issued + 300:
            raise ValueError("expired or invalid registration lifetime")
        endpoint = payload["endpoint"]
        if endpoint != approved["endpoint"]:
            raise PermissionError("endpoint is not approved for this coordinator")
        if not isinstance(endpoint, dict) or set(endpoint) != {"host", "port", "path"}:
            raise ValueError("invalid endpoint")
        host = endpoint["host"]
        if not isinstance(host, str) or not host or len(host) > 253 or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789.-" for c in host):
            raise ValueError("invalid hostname")
        if type(endpoint["port"]) is not int or not 1 <= endpoint["port"] <= 65535 or endpoint["path"] != MESH_PATH:
            raise ValueError("invalid endpoint port or path")
        return payload

    def _records(self):
        path = self.root / "registrations.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def register(self, announcement):
        with self.lock:
            payload = self.validate(announcement)
            records = self._records()
            previous = records.get(payload["node_id"])
            if previous and payload["issued_at"] <= previous["payload"]["issued_at"]:
                raise ValueError("registration replay")
            # Drop revoked records as well as expired ones; bound persistent state.
            active = {}
            for node_id, record in records.items():
                try:
                    self.validate(record)
                    active[node_id] = record
                except (ValueError, PermissionError, KeyError, TypeError):
                    pass
            active[payload["node_id"]] = announcement
            atomic_write_secret(self.root / "registrations.json", json.dumps(active).encode())

    def manifest(self):
        with self.lock:
            endpoints = []
            for record in self._records().values():
                try:
                    payload = self.validate(record)
                    endpoints.append({"node_id": payload["node_id"], **payload["endpoint"],
                                      "expires_at": payload["expires_at"]})
                except (ValueError, PermissionError, KeyError, TypeError):
                    continue
            return {"version": 1, "cluster_id": self.cluster_id,
                    "expires_at": int(self.clock()) + 120,
                    "coordinators": sorted(endpoints, key=lambda item: item["node_id"])}


def announcement(signer, cluster_id, endpoint, now=None):
    now = int(time.time() if now is None else now)
    payload = {"node_id": signer.node_id, "cluster_id": cluster_id,
               "endpoint": endpoint, "issued_at": now, "expires_at": now + 300}
    return {"payload": payload, "signature": signer.sign_message(payload)}


class BootstrapHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 32

    def __init__(self, address, directory, mesh):
        if address[0] != "127.0.0.1":
            raise ValueError("gateway must bind loopback behind HTTPS")
        self.directory, self.mesh = directory, mesh
        self.slots = threading.BoundedSemaphore(32)
        super().__init__(address, BootstrapHandler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class BootstrapHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, fmt, *args):
        # Never log request bodies, identities, signatures, or URL query tokens.
        pass

    def reply(self, status, value):
        body = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def do_GET(self):
        if self.path == DIRECTORY_PATH:
            try:
                self.reply(200, self.server.directory.manifest())
            except (OSError, ValueError, KeyError, TypeError):
                LOG.exception("directory unavailable")
                self.reply(503, {"error": "directory unavailable"})
        elif self.path == "/healthz":
            self.reply(200, {"status": "ok"})
        elif self.path == MESH_PATH:
            if self.headers.get("Upgrade", "").lower() != "zdx-mesh/1" or "upgrade" not in self.headers.get("Connection", "").lower():
                self.reply(426, {"error": "zdx-mesh/1 upgrade required"})
                return
            if self.headers.get("Content-Length", "0") != "0" or self.headers.get("Transfer-Encoding"):
                self.reply(400, {"error": "upgrade cannot carry an HTTP body"})
                return
            self.send_response(101)
            self.send_header("Connection", "Upgrade")
            self.send_header("Upgrade", "zdx-mesh/1")
            self.end_headers()
            self.wfile.flush()
            self.close_connection = True
            self.server.mesh.handle_client(self.connection, self.client_address)
        else:
            self.reply(404, {"error": "not found"})

    def do_POST(self):
        if self.path != REGISTER_PATH:
            self.reply(404, {"error": "not found"})
            return
        try:
            if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) != 1:
                raise ValueError("one content length required")
            length = int(self.headers["Content-Length"])
            if not 0 < length <= MAX_BODY:
                raise ValueError("invalid registration size")
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("truncated registration")
            self.server.directory.register(json.loads(raw))
            self.reply(200, {"registered": True})
        except PermissionError:
            self.reply(403, {"error": "coordinator not authorized"})
        except (ValueError, KeyError, TypeError):
            self.reply(400, {"error": "invalid registration"})
        except OSError:
            LOG.exception("registration storage unavailable")
            self.reply(503, {"error": "registration unavailable"})


class EnrolledGatewayServer(ZDXServer):
    """Reload operator enrollment atomically without restarting active clients."""

    def __init__(self, *args, peers_file: Path, **kwargs):
        self.peers_file = peers_file
        self.message_lock = threading.RLock()
        super().__init__(*args, **kwargs)
        self._pending_enrollment_lock = threading.RLock()

    def _authenticate(self, message):
        # A key must already have been approved by the operator. The initial
        # unknown identity is handled by the existing pending-enrollment queue.
        peers = json.loads(self.peers_file.read_text())
        self._peer_public_keys = peers
        self._enrolled_peers = set(peers)
        super()._authenticate(message)
        if message.kind in self._CLUSTER_KINDS:
            approved = json.loads((self.peers_file.parent / "coordinators.json").read_text())
            if approved.get(message.peer_id, {}).get("public_key") != peers.get(message.peer_id):
                raise PermissionError("only enrolled coordinators may exchange cluster state")
        if message.kind in {"compute_submit", "artifact_put", "artifact_upload_begin", "artifact_upload_chunk", "artifact_upload_finish"}:
            path = self.peers_file.parent / "submitters.json"
            submitters = json.loads(path.read_text()) if path.exists() else []
            if message.peer_id not in submitters:
                raise PermissionError("node is not authorized to submit work")

    def _queue_pending_enrollment(self, conn, message, address):
        if not isinstance(message.peer_id, str) or not 1 <= len(message.peer_id) <= 128:
            raise PermissionError("invalid enrollment identity length")
        peers = json.loads(self.peers_file.read_text())
        self._peer_public_keys = peers
        self._enrolled_peers = set(peers)
        path = self.state.path.parent / "pending_enrollments.json"
        with self._pending_enrollment_lock:
            pending = json.loads(path.read_text()) if path.exists() else {}
            if message.peer_id not in peers and len(pending) >= 256 and message.peer_id not in pending:
                raise PermissionError("pending enrollment queue is full")
            return super()._queue_pending_enrollment(conn, message, address)

    def _handle_compute(self, conn, message):
        with self.message_lock:
            return super()._handle_compute(conn, message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--cluster-id", default="zdx-private-prod")
    parser.add_argument("--public-host", required=True)
    parser.add_argument("--public-port", type=int, default=443)
    parser.add_argument("--registry-url", help="HTTPS registration URL of the trusted mesh directory")
    parser.add_argument("--listen-port", type=int, default=8766)
    args = parser.parse_args()
    if args.registry_url and (urlsplit(args.registry_url).scheme != "https" or urlsplit(args.registry_url).username or urlsplit(args.registry_url).fragment):
        parser.error("registry URL must be HTTPS without credentials or fragment")
    logging.basicConfig(level=logging.INFO)
    root = args.state_dir
    root.mkdir(parents=True, exist_ok=True)
    peers_file = root / "peers.json"
    if not peers_file.exists():
        atomic_write_secret(peers_file, b"{}")
    signer = ZDXEd25519Signer(args.node_id, str(root / "keys"))
    directory = CoordinatorDirectory(root, args.cluster_id)
    # This initial operator enrollment is for this server only. Other servers
    # require their own approved keys and endpoints before registration.
    approved_file = root / "coordinators.json"
    endpoint = {"host": args.public_host, "port": args.public_port, "path": MESH_PATH}
    if not approved_file.exists():
        atomic_write_secret(approved_file, json.dumps({args.node_id: {
            "public_key": signer.get_public_key_pem(), "endpoint": endpoint}}).encode())
    mesh = EnrolledGatewayServer(
        peers_file=peers_file, require_auth=True, signer=signer,
        trusted_peers=json.loads(peers_file.read_text()), node_id=args.node_id,
        cluster_id=args.cluster_id, state_path=str(root / "node_state.json"),
        compute_state_path=str(root / "compute_state.json"), artifact_root=str(root / "artifacts"),
        karma_path=str(root / "karma.json"), cluster_state_path=str(root / "cluster_state.json"))
    mesh.running = True
    stop = threading.Event()

    def renew():
        while not stop.is_set():
            try:
                lease = announcement(signer, args.cluster_id, endpoint)
                directory.register(lease)
                if args.registry_url:
                    request = urllib.request.Request(args.registry_url, data=json.dumps(lease).encode(),
                                                     headers={"Content-Type": "application/json"}, method="POST")
                    with urllib.request.urlopen(request, timeout=5) as response:
                        if response.status != 200:
                            raise ValueError("registry rejected announcement")
            except (ValueError, PermissionError, OSError):
                LOG.exception("coordinator lease refresh failed")
            stop.wait(60)

    http = BootstrapHTTPServer(("127.0.0.1", args.listen_port), directory, mesh)
    thread = threading.Thread(target=renew, daemon=True)
    thread.start()
    LOG.info("coordinator gateway listening on loopback:%s", args.listen_port)
    try:
        http.serve_forever()
    finally:
        stop.set()
        mesh.running = False
        http.server_close()


if __name__ == "__main__":
    main()
