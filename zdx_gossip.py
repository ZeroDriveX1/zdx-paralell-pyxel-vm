"""Authenticated periodic state gossip retaining sequence counters across reconnects."""

from __future__ import annotations

import socket
import threading
from typing import Iterable, Optional

from zdx_cluster import KarmicClusterRuntime, ReplicatedClusterState
from zdx_network import ZDXMessage
from zdx_node import ZDXNode


class StateGossipAgent:
    def __init__(self, runtime: KarmicClusterRuntime | None, state: ReplicatedClusterState,
                 node_id: str, signer, peers: Iterable[tuple[str, int]], *,
                 tls_context=None, interval_seconds: float = 5.0):
        self.runtime = runtime; self.state = state; self.node_id = node_id; self.signer = signer
        self.peers = list(peers); self.tls_context = tls_context
        self.interval_seconds = max(1.0, float(interval_seconds))
        self._stop = threading.Event(); self._thread: Optional[threading.Thread] = None
        self._nodes: dict[tuple[str, int], ZDXNode] = {}

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="zdx-state-gossip", daemon=True); self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0); self._thread = None

    def _run(self):
        while not self._stop.is_set():
            for host, port in self.peers:
                if self._stop.is_set(): break
                try: self._exchange(host, port)
                except (ConnectionError, OSError, socket.timeout, ValueError, RuntimeError): continue
            self._stop.wait(self.interval_seconds)

    def _exchange(self, host: str, port: int):
        key = (host, port)
        node = self._nodes.setdefault(key, ZDXNode(host=host, port=port, node_id=self.node_id, signer=self.signer, tls_context=self.tls_context))
        sock = None
        try:
            sock = node.connect(timeout=5.0)
            if node.request(sock, ZDXMessage(kind="cluster_state_push", payload={"snapshot": self.state.snapshot()})).kind != "cluster_state_ack":
                raise RuntimeError("peer rejected cluster state")
            reply = node.request(sock, ZDXMessage(kind="cluster_state_pull", payload={}))
            if reply.kind == "cluster_state_snapshot": self.state.merge(reply.payload["snapshot"])
        finally:
            if sock is not None: sock.close()
