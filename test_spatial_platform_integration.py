import os
import tempfile

from zdx_capabilities import detect_capabilities, supports_required_features
from zdx_frame_manifest import ZDXFrameManifest
from zdx_network import frame_announce
from zdx_protocol import SPATIAL_PNG_FEATURE, envelope, is_compatible, spatial_descriptor
from zdx_spatial_frame import SpatialLayout
from zdx_sync import FrameSync
from zdx_workload_manifest import ZDXWorkloadManifest


def test_spatial_frame_manifest_preserves_exact_frame_identity_and_layout():
    frame_bytes = b"\x89PNG\r\n\x1a\nsynthetic-test-frame"
    layout = SpatialLayout(width=256, height=256, execution_rows=8)
    manifest = ZDXFrameManifest(frame_bytes, spatial_layout=layout)
    payload = manifest.payload()

    assert manifest.verify(frame_bytes)
    assert payload["execution_model"] == "spatial-png"
    assert payload["spatial_version"] == 1
    assert payload["spatial_layout"]["width"] == 256
    assert payload["spatial_layout"]["height"] == 256
    assert payload["spatial_layout"]["execution_rows"] == 8


def test_workload_manifest_infers_spatial_feature_requirement():
    layout = SpatialLayout(width=64, height=32, execution_rows=4)
    frame = ZDXFrameManifest(b"frame", spatial_layout=layout)
    workload = ZDXWorkloadManifest("job-1", frame)
    payload = workload.payload()

    assert payload["execution_model"] == "spatial-png"
    assert SPATIAL_PNG_FEATURE in payload["required_vm_features"]
    assert payload["spatial_layout"]["width"] == 64


def test_protocol_remains_v1_compatible_and_negotiates_spatial_feature():
    layout = SpatialLayout(width=32, height=16, execution_rows=2)
    descriptor = spatial_descriptor(layout)
    message = envelope("workload", descriptor, features=[SPATIAL_PNG_FEATURE])

    assert is_compatible(message)
    assert message["features"] == [SPATIAL_PNG_FEATURE]
    assert message["payload"]["layout"]["height"] == 16


def test_node_capability_advertises_spatial_vm_support():
    capabilities = detect_capabilities()
    assert SPATIAL_PNG_FEATURE in capabilities.vm_features
    assert supports_required_features(capabilities, [SPATIAL_PNG_FEATURE, "xy-addressing"])
    assert not supports_required_features(capabilities, ["future-spatial-v99"])


def test_frame_sync_announces_spatial_geometry_without_rewriting_frame():
    layout = SpatialLayout(width=16, height=8, execution_rows=1)
    with tempfile.NamedTemporaryFile(delete=False) as fh:
        fh.write(b"exact-frame-bytes")
        path = fh.name
    try:
        original = open(path, "rb").read()
        sync = FrameSync()
        message = sync.announce(path, spatial_layout=layout)

        assert open(path, "rb").read() == original
        assert message.payload["execution_model"] == "spatial-png"
        assert message.payload["spatial_version"] == 1
        assert message.payload["spatial_layout"]["execution_rows"] == 1
        assert sync.verify(path, message.payload["sha256"])
    finally:
        os.unlink(path)


def test_network_frame_announce_carries_optional_spatial_contract():
    layout = SpatialLayout(width=128, height=64, execution_rows=8)
    message = frame_announce("program.png", "abc123", spatial_layout=layout)
    assert message.payload["execution_model"] == "spatial-png"
    assert message.payload["spatial_version"] == 1
    assert message.payload["spatial_layout"]["width"] == 128

    legacy = frame_announce("program.png", "abc123")
    assert legacy.payload["execution_model"] == "pixel-frame"
    assert "spatial_layout" not in legacy.payload
