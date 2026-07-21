"""
ZDX Ed25519 Asymmetric Signature Module.

Implements production-grade Ed25519 public-key cryptography for network authentication.

Instead of HMAC (shared secret), nodes use Ed25519 keypairs:
- Private key: kept secure on node, never transmitted
- Public key: shared during enrollment handshake
- Signature: proves message authenticity and non-repudiation

Usage:
    # On node startup
    signer = ZDXEd25519Signer(node_id="node_123", key_path=".zdx/keys/node_123")
    
    # Sign a message
    signature = signer.sign_message(payload)
    
    # Verify a peer's message
    verified = signer.verify_message(peer_id, payload, signature)

This module replaces HMAC-SHA256 to provide:
- Public-key cryptography (asymmetric)
- Non-repudiation (signer cannot deny signing)
- Better key management (no shared secrets)
- Industry standard Ed25519 (RFC 8032)
"""

from __future__ import annotations

import json
import base64
from pathlib import Path
from typing import Dict, Optional, Tuple
from dataclasses import dataclass

try:
    from cryptography.hazmat.primitives import serialization, hashes
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.backends import default_backend
    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False


@dataclass
class Ed25519KeyPair:
    """Ed25519 public/private keypair."""
    node_id: str
    private_key_pem: str  # PEM-encoded private key
    public_key_pem: str   # PEM-encoded public key
    created_at: float
    key_version: int = 1


class ZDXEd25519Signer:
    """
    Ed25519 signer for node identity and message authentication.

    Each node maintains a persistent Ed25519 keypair. The private key
    signs all outgoing messages. Peers verify signatures using the node's
    public key (shared during enrollment).
    """

    def __init__(self, node_id: str, key_path: str = ".zdx/keys"):
        """
        Initialize Ed25519 signer for a node.

        Args:
            node_id: Unique node identifier
            key_path: Directory to store keys

        Raises:
            ImportError: If cryptography library is not installed
        """
        if not HAS_CRYPTOGRAPHY:
            raise ImportError(
                "cryptography library required: pip install cryptography"
            )

        self.node_id = node_id
        self.key_path = Path(key_path)
        self.key_path.mkdir(parents=True, exist_ok=True)

        self._keypair: Optional[Ed25519KeyPair] = None
        self._peer_public_keys: Dict[str, str] = {}  # node_id -> public key PEM

        self.load_or_generate_keys()

    def load_or_generate_keys(self) -> None:
        """Load existing keypair or generate new one."""
        key_file = self.key_path / f"{self.node_id}.json"

        if key_file.exists():
            self._load_keypair(key_file)
        else:
            self._generate_keypair(key_file)

    def _generate_keypair(self, key_file: Path) -> None:
        """Generate new Ed25519 keypair and persist it."""
        import time

        # Generate keypair
        private_key = ed25519.Ed25519PrivateKey.generate()
        public_key = private_key.public_key()

        # Serialize to PEM
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")

        public_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")

        # Create keypair record
        self._keypair = Ed25519KeyPair(
            node_id=self.node_id,
            private_key_pem=private_pem,
            public_key_pem=public_pem,
            created_at=time.time(),
            key_version=1,
        )

        # Persist
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_data = {
            "node_id": self._keypair.node_id,
            "private_key_pem": self._keypair.private_key_pem,
            "public_key_pem": self._keypair.public_key_pem,
            "created_at": self._keypair.created_at,
            "key_version": self._keypair.key_version,
        }
        key_file.write_text(json.dumps(key_data, indent=2))

    def _load_keypair(self, key_file: Path) -> None:
        """Load keypair from disk."""
        key_data = json.loads(key_file.read_text())
        self._keypair = Ed25519KeyPair(**key_data)

    def get_public_key_pem(self) -> str:
        """Get public key in PEM format (for sharing with peers)."""
        if not self._keypair:
            raise RuntimeError("Keypair not initialized")
        return self._keypair.public_key_pem

    def register_peer_public_key(self, peer_id: str, public_key_pem: str) -> None:
        """
        Register a peer's public key for signature verification.

        Args:
            peer_id: Peer node ID
            public_key_pem: Peer's public key in PEM format
        """
        self._peer_public_keys[peer_id] = public_key_pem

    def sign_message(self, payload: dict) -> str:
        """
        Sign a message payload using private key.

        Args:
            payload: Message to sign (will be JSON-canonicalized)

        Returns:
            Base64-encoded signature
        """
        if not self._keypair:
            raise RuntimeError("Keypair not initialized")

        # Canonicalize payload
        message = json.dumps(payload, sort_keys=True, separators=(",", ":"))

        # Load private key
        private_key = serialization.load_pem_private_key(
            self._keypair.private_key_pem.encode(),
            password=None,
            backend=default_backend(),
        )

        # Sign
        signature_bytes = private_key.sign(message.encode())

        # Return base64-encoded signature
        return base64.b64encode(signature_bytes).decode("utf-8")

    def verify_message(self, peer_id: str, payload: dict, signature_b64: str) -> bool:
        """
        Verify a peer's message signature.

        Args:
            peer_id: Peer node ID
            payload: Message payload
            signature_b64: Base64-encoded signature

        Returns:
            True if signature is valid, False otherwise
        """
        if peer_id not in self._peer_public_keys:
            return False

        try:
            # Canonicalize payload
            message = json.dumps(payload, sort_keys=True, separators=(",", ":"))

            # Load peer's public key
            public_key = serialization.load_pem_public_key(
                self._peer_public_keys[peer_id].encode(),
                backend=default_backend(),
            )

            # Decode signature
            signature_bytes = base64.b64decode(signature_b64)

            # Verify
            public_key.verify(signature_bytes, message.encode())
            return True

        except Exception as e:
            return False

    def verify_self_signature(self, payload: dict, signature_b64: str) -> bool:
        """
        Verify a signature using this node's public key.

        Useful for testing and internal verification.

        Args:
            payload: Message payload
            signature_b64: Base64-encoded signature

        Returns:
            True if signature is valid, False otherwise
        """
        return self.verify_message(self.node_id, payload, signature_b64)

    def rotate_keys(self) -> str:
        """
        Rotate to a new keypair (revoke old one).

        Returns:
            Filename of old keypair backup
        """
        import time

        if not self._keypair:
            raise RuntimeError("Keypair not initialized")

        old_key_file = self.key_path / f"{self.node_id}.json"
        backup_file = self.key_path / f"{self.node_id}.{int(time.time())}.bak"

        # Backup old key
        if old_key_file.exists():
            backup_file.write_text(old_key_file.read_text())

        # Generate new key
        self._generate_keypair(old_key_file)

        print(f"[KEY ROTATION] Node {self.node_id} rotated keys")
        print(f"               Old key backed up: {backup_file}")

        return str(backup_file)

    def get_key_version(self) -> int:
        """Get current key version (useful for key rotation tracking)."""
        if not self._keypair:
            raise RuntimeError("Keypair not initialized")
        return self._keypair.key_version


class ZDXEnrollmentSigner:
    """
    Separate signer for enrollment handshake messages.

    During enrollment, a node exchanges its public key with the coordinator.
    This signer signs enrollment requests and verifies enrollment responses.
    """

    def __init__(self, node_signer: ZDXEd25519Signer):
        """
        Initialize enrollment signer using node's keypair.

        Args:
            node_signer: Node's Ed25519Signer instance
        """
        self.node_signer = node_signer

    def create_enrollment_request(self, capabilities: dict) -> Tuple[dict, str]:
        """
        Create signed enrollment request.

        Args:
            capabilities: Node capabilities (compute, storage, etc.)

        Returns:
            (payload_dict, signature_b64)
        """
        payload = {
            "node_id": self.node_signer.node_id,
            "public_key": self.node_signer.get_public_key_pem(),
            "capabilities": capabilities,
        }

        signature = self.node_signer.sign_message(payload)
        return payload, signature

    def verify_enrollment_response(
        self, coordinator_id: str, response_payload: dict, signature_b64: str
    ) -> bool:
        """
        Verify coordinator's enrollment response signature.

        Args:
            coordinator_id: Coordinator node ID
            response_payload: Response from coordinator
            signature_b64: Coordinator's signature

        Returns:
            True if signature is valid and from coordinator
        """
        return self.node_signer.verify_message(
            coordinator_id, response_payload, signature_b64
        )


# Helper function for testing without cryptography library
def create_stub_signer(node_id: str) -> Dict:
    """
    Create a stub signer for testing without cryptography library.

    WARNING: For testing only! Not secure.

    Returns:
        Dict with sign/verify methods
    """

    def stub_sign(payload: dict) -> str:
        import hashlib
        msg = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(msg.encode()).hexdigest()

    def stub_verify(peer_id: str, payload: dict, sig: str) -> bool:
        # Stub: always succeed for testing
        return True

    return {
        "node_id": node_id,
        "sign_message": stub_sign,
        "verify_message": stub_verify,
        "get_public_key_pem": lambda: f"STUB_KEY_{node_id}",
    }
