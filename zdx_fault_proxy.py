"""Deterministic TCP fault proxy for deployment validation only."""

from __future__ import annotations

import random
import socket
import threading
import time


class TCPFaultProxy:
    def __init__(
        self, target_host, target_port, *, latency=0.0, jitter=0.0,
        drop_every=0, duplicate_every=0, reorder_every=0, seed=14,
    ):
        self.target = (target_host, target_port)
        self.latency = latency
        self.jitter = jitter
        self.drop_every = drop_every
        self.duplicate_every = duplicate_every
        self.reorder_every = reorder_every
        self.random = random.Random(seed)
        self.running = False
        self.port = 0
        self._listener = None
        self._thread = None
        self._connections = set()
        self._lock = threading.RLock()

    def start(self):
        self.running = True
        self._listener = socket.socket()
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self.port = self._listener.getsockname()[1]
        self._listener.listen()
        self._listener.settimeout(0.1)
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()
        return self

    def _accept(self):
        while self.running:
            try:
                client, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                upstream = socket.create_connection(self.target, timeout=2)
            except OSError:
                client.close()
                continue
            with self._lock:
                self._connections.update((client, upstream))
            threading.Thread(
                target=self._pump, args=(client, upstream), daemon=True
            ).start()
            threading.Thread(
                target=self._pump, args=(upstream, client), daemon=True
            ).start()

    def _pump(self, source, destination):
        count = 0
        held = None
        try:
            while self.running:
                data = source.recv(4096)
                if not data:
                    break
                count += 1
                if self.drop_every and count % self.drop_every == 0:
                    continue
                delay = self.latency
                if self.jitter:
                    delay += self.random.uniform(0, self.jitter)
                if delay:
                    time.sleep(delay)
                if self.reorder_every and count % self.reorder_every == 0:
                    if held is None:
                        held = data
                        continue
                    destination.sendall(data)
                    destination.sendall(held)
                    held = None
                else:
                    destination.sendall(data)
                if self.duplicate_every and count % self.duplicate_every == 0:
                    destination.sendall(data)
            if held:
                destination.sendall(held)
        except OSError:
            pass
        finally:
            self._close_pair(source, destination)

    def partition(self):
        """Drop all current connections while leaving the proxy restartable."""
        with self._lock:
            connections = list(self._connections)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()

    def _close_pair(self, *connections):
        with self._lock:
            for connection in connections:
                self._connections.discard(connection)
                try:
                    connection.close()
                except OSError:
                    pass

    def stop(self):
        self.running = False
        self.partition()
        if self._listener:
            self._listener.close()
        if self._thread:
            self._thread.join(1)
