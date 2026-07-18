"""
ZDX Android/mobile node client foundation.

Designed for ARM devices. Networking integration will be added in future passes.
"""

from __future__ import annotations

from zdx_mobile_profile import mobile_profile
from zdx_node_identity import ZDXNodeIdentity


class ZDXAndroidNode:
    def __init__(self):
        self.identity = ZDXNodeIdentity()
        self.profile = mobile_profile()

    def capability_payload(self):
        return {
            "node_id": self.identity.node_id,
            **self.profile,
            "client": "android",
        }

    def status(self):
        return {
            "node_id": self.identity.node_id,
            "profile": self.profile,
        }
