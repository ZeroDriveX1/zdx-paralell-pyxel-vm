"""Short-lived Ed25519 re-attestation challenges for enrolled peers."""

from __future__ import annotations

import secrets
import time
from pathlib import Path

from zdx_ed25519_signer import verify_ed25519_signature
from zdx_storage import StateStore

CHALLENGE_TTL_SECONDS = 120.0
MAX_ACTIVE_CHALLENGES = 10_000


class ReattestationChallengeStore:
    def __init__(self, path: str = ".zdx/reattest.json"):
        self.path = Path(path)
        self.store = StateStore(self.path, "reattest-challenges")
        self._state = self.store.load({"version": 1, "challenges": {}})

    def _save(self) -> None:
        self.store.save(self._state)

    @staticmethod
    def signed_payload(challenge: dict) -> dict:
        return {
            "domain": "zdx-reattest-v1",
            "request_id": challenge["request_id"],
            "node_id": challenge["node_id"],
            "nonce": challenge["nonce"],
            "issued_at": float(challenge["issued_at"]),
            "expires_at": float(challenge["expires_at"]),
        }

    def cleanup(self, now: float | None = None) -> int:
        current = time.time() if now is None else float(now)
        challenges = self._state.setdefault("challenges", {})
        expired = [
            key for key, value in challenges.items()
            if float(value.get("expires_at", 0.0)) <= current
            or value.get("status") in {"verified", "failed", "expired"}
        ]
        for key in expired:
            challenges.pop(key, None)
        if expired:
            self._save()
        return len(expired)

    def issue(
        self,
        *,
        request_id: str,
        node_id: str,
        ttl_seconds: float = CHALLENGE_TTL_SECONDS,
        now: float | None = None,
    ) -> dict:
        current = time.time() if now is None else float(now)
        ttl = float(ttl_seconds)
        if ttl <= 0 or ttl > 600:
            raise ValueError("reattest challenge TTL must be in (0, 600]")
        self.cleanup(current)
        challenges = self._state.setdefault("challenges", {})
        if len(challenges) >= MAX_ACTIVE_CHALLENGES:
            raise ValueError("too many active re-attestation challenges")
        existing = next(
            (
                item for item in challenges.values()
                if item.get("request_id") == request_id
                and item.get("node_id") == node_id
                and item.get("status") == "pending"
                and float(item.get("expires_at", 0.0)) > current
            ),
            None,
        )
        if existing is not None:
            return dict(existing)
        challenge_id = secrets.token_hex(16)
        record = {
            "challenge_id": challenge_id,
            "request_id": str(request_id),
            "node_id": str(node_id),
            "nonce": secrets.token_urlsafe(32),
            "issued_at": current,
            "expires_at": current + ttl,
            "status": "pending",
            "attempts": 0,
        }
        challenges[challenge_id] = record
        self._save()
        return dict(record)

    def verify(
        self,
        *,
        challenge_id: str,
        node_id: str,
        signature: str,
        public_key_pem: str,
        now: float | None = None,
    ) -> dict:
        current = time.time() if now is None else float(now)
        challenges = self._state.setdefault("challenges", {})
        record = challenges.get(str(challenge_id))
        if record is None:
            raise KeyError("unknown re-attestation challenge")
        if record.get("status") != "pending":
            raise ValueError("re-attestation challenge is not pending")
        if record.get("node_id") != str(node_id):
            raise PermissionError("challenge does not belong to this node")
        if float(record.get("expires_at", 0.0)) <= current:
            record["status"] = "expired"
            self._save()
            raise TimeoutError("re-attestation challenge expired")
        record["attempts"] = int(record.get("attempts", 0)) + 1
        payload = self.signed_payload(record)
        if not verify_ed25519_signature(public_key_pem, payload, signature):
            record["status"] = "failed"
            record["failed_at"] = current
            self._save()
            raise PermissionError("invalid re-attestation signature")
        record["status"] = "verified"
        record["verified_at"] = current
        self._save()
        return dict(record)
