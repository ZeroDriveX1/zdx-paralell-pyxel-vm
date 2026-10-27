"""Negotiation for the APK-bundled, bounded Android Pyxel interpreter."""

import hashlib

ADAPTER_ID = "zdx.pyxel.android"
DESCRIPTOR = "zdx.pyxel.android:v1:static-rgb8:1048576-pixels:32-rows:4096-steps:8MiB:64-512MiB:300s:no-chain:no-host"
MANIFEST_SHA256 = hashlib.sha256(DESCRIPTOR.encode("ascii")).hexdigest()
FEATURE = "pyxel-static-rgb-v1"


def compatible_android_task(task, capabilities):
    """Never route an ordinary/unsupported desktop task to a mobile worker."""
    adapter_id = task.metadata.get("android_vm_adapter_id")
    if task.metadata.get("android_bounded_probe") is True and not adapter_id:
        adapter_id = "zdx.integrity-probe"
    if capabilities.get("platform") != "android":
        return not adapter_id
    if not adapter_id or not task.artifact_digest:
        return False
    if capabilities.get("android_vm_adapter_protocol") != 1:
        return False
    adapters = capabilities.get("android_vm_adapters", [])
    if not isinstance(adapters, list):
        return False
    adapter = next((item for item in adapters if isinstance(item, dict) and item.get("adapter_id") == adapter_id), None)
    if not adapter or adapter.get("protocol_version") != 1:
        return False
    required = task.metadata.get("required_android_capabilities", [])
    available = adapter.get("capabilities", [])
    if not isinstance(required, list) or not isinstance(available, list) or any(not isinstance(value, str) or value not in available for value in required):
        return False
    for key, requested in (("max_memory_mb", task.memory_mb), ("max_runtime_seconds", task.max_seconds)):
        limit = adapter.get(key)
        if isinstance(limit, bool) or not isinstance(limit, int) or requested > limit:
            return False
    if adapter_id == ADAPTER_ID:
        return (
            adapter.get("manifest_sha256") == MANIFEST_SHA256
            and task.metadata.get("execution_model", "pixel-frame") == "pixel-frame"
            and task.metadata.get("artifact_type", "pyxel-frame-v1") == "pyxel-frame-v1"
            and not {"frame_registry", "opcode_map"}.intersection(task.metadata)
            and 64 <= task.memory_mb <= 512 and task.threads <= 32 and task.max_seconds <= 300
            and FEATURE in available
        )
    return adapter_id == "zdx.integrity-probe"
