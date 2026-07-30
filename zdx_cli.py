"""
ZDX Parallel Pyxel VM node CLI.

Commands:
  serve  - start network node
  ping   - test node heartbeat
  hash   - calculate frame checksum
"""

import argparse
import socket

from zdx_server import ZDXServer
from zdx_network import ZDXMessage, send_message, recv_message
from zdx_sync import FrameSync


def main():
    parser = argparse.ArgumentParser(description="ZDX Pyxel VM Node")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="start the local development node")
    serve.add_argument("--port", type=int, default=8765, help="listen port (default: 8765)")

    ping = sub.add_parser("ping", help="send a heartbeat to a local node")
    ping.add_argument("host", help="node hostname or IP address")
    ping.add_argument("--port", type=int, default=8765, help="node port (default: 8765)")

    frame = sub.add_parser("hash", help="print the SHA-256 digest of a frame")
    frame.add_argument("path", help="path to the frame file")

    args = parser.parse_args()

    if args.command == "serve":
        ZDXServer(port=args.port).serve()

    elif args.command == "ping":
        with socket.create_connection((args.host, args.port)) as sock:
            send_message(sock, ZDXMessage(kind="heartbeat", payload={}))
            print(recv_message(sock))

    elif args.command == "hash":
        print(FrameSync.checksum(args.path))


if __name__ == "__main__":
    main()
