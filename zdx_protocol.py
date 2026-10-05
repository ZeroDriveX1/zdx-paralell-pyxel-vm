"""Compatibility exports for the canonical authenticated ZDX protocol."""

from zdx_network import PROTOCOL_VERSION, ZDXMessage

SPATIAL_FRAME_VERSION = 1
SPATIAL_PNG_FEATURE = "spatial-png-v1"


def envelope(message_type, payload=None, **authenticated_fields):
    """Build the canonical envelope; callers must sign it before transport."""
    return ZDXMessage(message_type, payload or {}, **authenticated_fields)


def is_compatible(message):
    version = message.version if isinstance(message, ZDXMessage) else message.get(
        "version", message.get("protocol_version")
    )
    return version == PROTOCOL_VERSION


def spatial_descriptor(layout) -> dict:
    """Return authenticated-payload metadata for a spatial PNG contract."""
    if hasattr(layout, "to_dict"):
        layout = layout.to_dict()
    if not isinstance(layout, dict):
        raise TypeError("layout must be a dict or expose to_dict()")
    required = {"width", "height", "execution_rows"}
    missing = sorted(required.difference(layout))
    if missing:
        raise ValueError(f"spatial layout missing required fields: {', '.join(missing)}")
    return {
        "feature": SPATIAL_PNG_FEATURE,
        "spatial_version": SPATIAL_FRAME_VERSION,
        "layout": dict(layout),
    }
