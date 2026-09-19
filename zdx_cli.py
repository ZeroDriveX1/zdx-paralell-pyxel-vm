"""Command-line entry points for authenticated ZDX networking."""

import argparse
from pathlib import Path

from security.identity import NodeIdentity
from zdx_node import ZDXNode
from zdx_server import ZDXServer
from zdx_session import NodeCredentials
from zdx_sync import FrameSync
from zdx_tls import TLSConfig


def _trust_spec(value: str) -> tuple[str, bytes]:
    try:
        node_id, path = value.split("=", 1)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected NODE_ID=PUBLIC_KEY_FILE") from exc
    data = Path(path).read_bytes()
    if len(data) != 32:
        raise argparse.ArgumentTypeError("Ed25519 public key file must be 32 raw bytes")
    return node_id, data


def main():
    parser = argparse.ArgumentParser(description="ZDX Pyxel VM Node")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="start an authenticated coordinator")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--coordinator-key", default=".zdx/coordinator_key.pem")
    serve.add_argument("--tls-ca", required=True)
    serve.add_argument("--tls-cert", required=True)
    serve.add_argument("--tls-key", required=True)
    serve.add_argument("--tls-crl")
    serve.add_argument(
        "--trust", action="append", default=[], type=_trust_spec,
        metavar="NODE_ID=PUBLIC_KEY_FILE",
        help="pin an enrolled node; repeat for multiple nodes",
    )

    ping = sub.add_parser("ping", help="send an authenticated heartbeat")
    ping.add_argument("host")
    ping.add_argument("--port", type=int, default=8765)
    ping.add_argument("--node-key", default=".zdx/node_key.pem")
    ping.add_argument("--coordinator-id", required=True)
    ping.add_argument("--coordinator-public-key", required=True, type=Path)
    ping.add_argument("--tls-ca", required=True)
    ping.add_argument("--tls-cert", required=True)
    ping.add_argument("--tls-key", required=True)
    ping.add_argument("--tls-crl")
    ping.add_argument("--server-hostname", required=True)

    frame = sub.add_parser("hash", help="print the SHA-256 digest of a frame")
    frame.add_argument("path")
    args = parser.parse_args()

    if args.command == "serve":
        credentials = NodeCredentials(
            NodeIdentity.load_or_create(args.coordinator_key)
        )
        server = ZDXServer(
            port=args.port,
            credentials=credentials,
            tls_config=TLSConfig(
                args.tls_ca, args.tls_cert, args.tls_key,
                crl_file=args.tls_crl,
            ),
        )
        for node_id, public_key in args.trust:
            server.enroll(node_id, public_key)
        server.serve()
    elif args.command == "ping":
        node = ZDXNode(
            host=args.host, port=args.port, key_path=args.node_key,
            tls_config=TLSConfig(
                args.tls_ca, args.tls_cert, args.tls_key,
                crl_file=args.tls_crl,
            ),
            server_hostname=args.server_hostname,
        )
        node.trust_coordinator(
            args.coordinator_id, args.coordinator_public_key.read_bytes()
        )
        with node.connect() as sock:
            print(node.ping(sock))
    elif args.command == "hash":
        print(FrameSync.checksum(args.path))


if __name__ == "__main__":
    main()
