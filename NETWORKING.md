# ZDX Parallel Pyxel VM Networking

## Architecture

The networking layer wraps the deterministic Pyxel VM without changing execution.

```
zdx_network.py
      |
zdx_node.py
      |
zdx_server.py
      |
zdx_sync.py
      |
zdx_cli.py
```

## Node Commands

Start a node:

```bash
python zdx_cli.py serve --port 8765
```

Ping a node:

```bash
python zdx_cli.py ping 127.0.0.1
```

Hash a frame:

```bash
python zdx_cli.py hash program.png
```

## Synchronization Model

Frames are identified by SHA-256 checksums.

Nodes exchange:

- identity messages
- heartbeat messages
- frame manifests
- integrity verification results

Network transport moves data only. VM execution remains deterministic and local.

## Attribution

Developed by ZeroDriveX LLC.

https://zerodrivex.com
