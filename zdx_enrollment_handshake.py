"""
ZDX enrollment handshake flow.

Connects node identity reports with enrollment approval state.
"""

from __future__ import annotations

from zdx_enrollment import ZDXEnrollment
from zdx_node_signature import ZDXNodeSignature


class ZDXEnrollmentHandshake:
    def __init__(self):
        self.enrollment = ZDXEnrollment()

    def request(self, identity, capabilities):
        signer = ZDXNodeSignature(identity.node_id)
        payload = signer.identity_payload(capabilities)
        return self.enrollment.request(identity.node_id, payload)

    def approve(self, node_id):
        self.enrollment.approve(node_id)

    def status(self, node_id):
        return self.enrollment.status(node_id)
