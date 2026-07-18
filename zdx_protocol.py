"""
ZDX protocol metadata helpers.

Adds common fields for future network compatibility checks.
"""

from __future__ import annotations

import time


PROTOCOL_VERSION = 1


def envelope(message_type, payload=None):
    return {
        "protocol_version": PROTOCOL_VERSION,
        "type": message_type,
        "timestamp": time.time(),
        "payload": payload or {},
    }


def is_compatible(message):
    return message.get("protocol_version") == PROTOCOL_VERSION
