"""Secure identity key storage primitives."""

import os
from pathlib import Path


class KeyStore:
    def __init__(self, directory: str = "~/.open_pyxel/identity"):
        self.directory = Path(directory).expanduser()
        self.key_file = self.directory / "node_key.pem"

    def prepare(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            self.directory.chmod(0o700)
        except OSError:
            pass

    def save(self, data: bytes):
        self.prepare()
        self.key_file.write_bytes(data)
        try:
            self.key_file.chmod(0o600)
        except OSError:
            pass

    def load(self):
        if not self.key_file.exists():
            return None
        return self.key_file.read_bytes()

    def exists(self):
        return self.key_file.exists()
