# ZDX Protocol Layer

The canonical `ZDXMessage` envelope is defined in `zdx_network.py`. Its Ed25519 signature covers protocol version, message type, sender identity, session ID, sequence, timestamp, nonce, request ID, key version, payload checksum, and payload.

Inbound application packets require a mutually authenticated, unexpired session and pass strict identity, signature, timestamp, nonce, sequence, version, type, and checksum validation before dispatch. Handshake messages use signed client and coordinator challenges. Reconnect replaces the old session; restart requires fresh authentication.

Pass 13 locally exercised valid traffic, duplicate packets, reordered sequences, delayed timestamps, disconnect/reconnect state, session expiration, trust recovery, registry rebuild, and 500 deterministic malformed packet inputs. Physical packet loss, cross-machine clocks, multi-host TLS interoperability, and multi-host coordinator recovery remain unvalidated deployment concerns.
