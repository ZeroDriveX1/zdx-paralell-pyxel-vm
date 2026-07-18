"""
ZDX Parallel Pyxel VM TCP server.

Accepts transport connections and exchanges protocol messages.
The server intentionally does not execute received programs.
"""

from __future__ import annotations

import socket
import threading

from zdx_network import recv_message, send_message, ZDXMessage, heartbeat


class ZDXServer:
    def __init__(self, host="0.0.0.0", port=8765):
        self.host = host
        self.port = port
        self.running = False
        self.peers = {}

    def handle_client(self, conn, address):
        try:
            while self.running:
                message = recv_message(conn)
                if message.kind == "identity":
                    self.peers[address] = message.payload
                    send_message(
                        conn,
                        ZDXMessage(
                            kind="identity_ack",
                            payload={"accepted": True},
                        ),
                    )
                elif message.kind == "heartbeat":
                    send_message(conn, heartbeat())
                else:
                    send_message(
                        conn,
                        ZDXMessage(
                            kind="ack",
                            payload={"received": message.kind},
                        ),
                    )
        except (ConnectionError, OSError):
            self.peers.pop(address, None)
        finally:
            conn.close()

    def serve(self):
        self.running = True
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.host, self.port))
            server.listen()

            while self.running:
                conn, address = server.accept()
                thread = threading.Thread(
                    target=self.handle_client,
                    args=(conn, address),
                    daemon=True,
                )
                thread.start()

    def stop(self):
        self.running = False
