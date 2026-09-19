"""Long-running authenticated TLS worker connection."""

from __future__ import annotations

import argparse
import signal
import time
from pathlib import Path

from zdx_node import ZDXNode
from zdx_tls import TLSConfig


def main():
    parser = argparse.ArgumentParser(description="ZeroDriveX worker")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--node-key", required=True)
    parser.add_argument("--coordinator-id", required=True)
    parser.add_argument("--coordinator-public-key", required=True, type=Path)
    parser.add_argument("--tls-ca", required=True)
    parser.add_argument("--tls-cert", required=True)
    parser.add_argument("--tls-key", required=True)
    parser.add_argument("--tls-crl")
    parser.add_argument("--server-hostname", required=True)
    parser.add_argument("--heartbeat-seconds", type=float, default=15)
    parser.add_argument("--cpu-count", type=int, default=1)
    args = parser.parse_args()
    if args.heartbeat_seconds <= 0:
        parser.error("--heartbeat-seconds must be positive")

    stopping = False

    def stop(_signal, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    node = ZDXNode(
        host=args.host, port=args.port, key_path=args.node_key,
        capabilities={"cpu_count": max(1, args.cpu_count)},
        tls_config=TLSConfig(
            args.tls_ca, args.tls_cert, args.tls_key,
            crl_file=args.tls_crl,
        ),
        server_hostname=args.server_hostname,
    )
    node.trust_coordinator(
        args.coordinator_id, args.coordinator_public_key.read_bytes()
    )
    delay = 0.5
    while not stopping:
        try:
            with node.connect() as connection:
                delay = 0.5
                while not stopping:
                    if stopping:
                        break
                    time.sleep(args.heartbeat_seconds)
                    node.ping(connection)
        except (OSError, ConnectionError):
            if stopping:
                break
            time.sleep(delay)
            delay = min(delay * 2, 30.0)


if __name__ == "__main__":
    main()
