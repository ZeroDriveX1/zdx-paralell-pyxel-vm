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
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8765)

    ping = sub.add_parser("ping")
    ping.add_argument("host")
    ping.add_argument("--port", type=int, default=8765)

    frame = sub.add_parser("hash")
    frame.add_argument("path")

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
