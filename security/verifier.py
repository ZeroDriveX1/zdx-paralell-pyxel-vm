"""Signature verification helpers."""

import json


class MessageVerifier:
    def __init__(self, public_key):
        self.public_key = public_key

    def verify(self, message: dict) -> bool:
        signature = bytes.fromhex(message.pop("signature"))
        payload = json.dumps(message, sort_keys=True, separators=(",", ":")).encode()
        self.public_key.verify(signature, payload)
        return True
