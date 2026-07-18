"""
ZDX mobile node capability helpers.

Initial support for ARM-based devices such as Android nodes.
"""

from __future__ import annotations

import platform


def mobile_profile():
    architecture = platform.machine()
    return {
        "device_class": "mobile" if architecture.lower().startswith(("arm", "aarch")) else "desktop",
        "architecture": architecture,
        "battery_aware": True,
        "thermal_aware": True,
        "npu_candidate": architecture.lower().startswith(("arm", "aarch")),
    }
