"""Transactional private-key storage primitives."""
from pathlib import Path
from zdx_storage import StateStore, atomic_write_secret


class KeyStore:
    def __init__(self, directory: str = "~/.open_pyxel/identity"):
        self.directory = Path(directory).expanduser()
        self.key_file = self.directory / "node_key.pem"
        self.metadata = StateStore(
            self.directory / "key_metadata.json", "key-metadata"
        )

    def prepare(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        self.directory.chmod(0o700)

    def save(self, data: bytes):
        self.prepare()
        atomic_write_secret(self.key_file, data)
        self.metadata.save({
            "algorithm": "Ed25519", "secret_file": self.key_file.name
        })

    def load(self):
        if not self.key_file.exists():
            return None
        self.metadata.load({
            "algorithm": "Ed25519", "secret_file": self.key_file.name
        })
        return self.key_file.read_bytes()

    def exists(self):
        return self.key_file.exists()
