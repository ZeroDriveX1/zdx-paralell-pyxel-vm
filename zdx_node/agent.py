from dataclasses import dataclass
from platform import platform
import uuid
import time


@dataclass
class WorkloadAuthorization:
    allowed: bool
    reason: str = ""


class AndroidHandshakeAgent:
    """Handshake metadata adapter for Android nodes."""

    def create_handshake(self, device_info=None):
        return {
            "node_id": str(uuid.uuid4()),
            "platform": "android",
            "device": device_info or {},
            "timestamp": time.time(),
            "protocol": "zdx-node-v1",
        }


class NodeTransport:
    """Transport abstraction. Production implementations can use TLS/WebSocket/QUIC."""

    def __init__(self, endpoint=None):
        self.endpoint = endpoint

    def send(self, payload):
        return {"accepted": True, "payload": payload}


class WorkloadAuthorizer:
    def authorize(self, workload):
        if not workload:
            return WorkloadAuthorization(False, "empty workload")
        return WorkloadAuthorization(True, "authorized")


class ZDXNodeAgent:
    def __init__(self, endpoint=None):
        self.handshake = AndroidHandshakeAgent()
        self.transport = NodeTransport(endpoint)
        self.authorizer = WorkloadAuthorizer()

    def register(self, device_info=None):
        return self.transport.send(self.handshake.create_handshake(device_info))

    def submit_workload(self, workload):
        auth = self.authorizer.authorize(workload)
        if not auth.allowed:
            raise PermissionError(auth.reason)
        return self.transport.send({"workload": workload})
