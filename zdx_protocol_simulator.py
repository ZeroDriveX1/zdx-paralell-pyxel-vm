"""
ZDX protocol simulation helpers.

Used to test message flows without opening network sockets.
"""

from __future__ import annotations

from zdx_protocol import envelope, is_compatible


class ZDXProtocolSimulator:
    def send(self, message_type, payload=None):
        return envelope(message_type, payload)

    def validate(self, message):
        return is_compatible(message)
