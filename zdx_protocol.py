"""Compatibility exports for the canonical authenticated ZDX protocol."""

from zdx_network import PROTOCOL_VERSION, ZDXMessage

from zdx_spatial_frame import SPATIAL_FRAME_VERSION, SPATIAL_PNG_FEATURE, SpatialLayout


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
    canonical = SpatialLayout.from_dict(layout).to_dict()
    return {
        "feature": SPATIAL_PNG_FEATURE,
        "spatial_version": SPATIAL_FRAME_VERSION,
        "layout": canonical,
    }
