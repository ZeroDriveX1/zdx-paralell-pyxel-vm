"""Message signing helpers."""

import json


class MessageSigner:
    def __init__(self, identity):
        self.identity = identity

    def sign(self, message: dict) -> bytes:
        payload = json.dumps(message, sort_keys=True, separators=(",", ":")).encode()
        return self.identity.private_key.sign(payload)

    def envelope(self, payload: dict, timestamp: int, nonce: str, sequence: int):
        message = {
            "protocol_version": 1,
            "timestamp": timestamp,
            "nonce": nonce,
            "sequence": sequence,
            "node_id": self.identity.node_id,
            "payload": payload,
        }
        message["signature"] = self.sign(message).hex()
        return message
