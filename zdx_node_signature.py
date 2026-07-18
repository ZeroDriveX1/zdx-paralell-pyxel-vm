"""
ZDX node identity signing foundation.

Provides deterministic signing payload generation for future authenticated
node enrollment.
"""

from __future__ import annotations

import hashlib
import json


class ZDXNodeSignature:
    def __init__(self, node_id):
        self.node_id = node_id

    def payload_hash(self, capabilities):
        payload = {
            "node_id": self.node_id,
            "capabilities": capabilities,
        }
        encoded = json.dumps(payload, sort_keys=True).encode()
        return hashlib.sha256(encoded).hexdigest()

    def identity_payload(self, capabilities):
        return {
            "node_id": self.node_id,
            "capabilities": capabilities,
            "identity_hash": self.payload_hash(capabilities),
        }
