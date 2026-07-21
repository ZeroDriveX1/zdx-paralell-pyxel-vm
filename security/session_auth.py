"""Authenticated node session handshake primitives."""

import secrets


class SessionAuthenticator:
    def __init__(self, signer, verifier):
        self.signer = signer
        self.verifier = verifier
        self.challenge = None
        self.authenticated = False

    def create_hello(self):
        self.challenge = secrets.token_hex(32)
        return {"type": "HELLO", "challenge": self.challenge}

    def create_response(self):
        if not self.challenge:
            raise RuntimeError("No challenge available")
        return self.signer.envelope(
            {"type": "CHALLENGE_RESPONSE", "challenge": self.challenge},
            timestamp=0,
            nonce=secrets.token_hex(16),
            sequence=0,
        )

    def verify_response(self, message):
        self.verifier.verify(message)
        self.authenticated = True
        return True
