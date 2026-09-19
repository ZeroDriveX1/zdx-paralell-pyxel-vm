"""Compatibility exports for the canonical authenticated ZDX protocol."""

from zdx_network import PROTOCOL_VERSION, ZDXMessage


def envelope(message_type, payload=None, **authenticated_fields):
    """Build the canonical envelope; callers must sign it before transport."""
    return ZDXMessage(message_type, payload or {}, **authenticated_fields)


def is_compatible(message):
    version = message.version if isinstance(message, ZDXMessage) else message.get(
        "version", message.get("protocol_version")
    )
    return version == PROTOCOL_VERSION
