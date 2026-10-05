"""Durable advisory queue for peer revalidation / rectification requests.

Rectification is deliberately non-authoritative. A request means "re-attest or
review this enrolled peer"; it never suspends, revokes, penalizes, or changes
karma by itself. Enforcement remains with verified evidence and cluster policy.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from pathlib import Path
from typing import Optional

from zdx_storage import StateStore

MAX_PENDING = 10_000
MAX_DETAIL = 512
REPORTER_WINDOW_SECONDS = 3600
MAX_REPORTS_PER_WINDOW = 20
COALESCE_WRITE_INTERVAL_SECONDS = 60.0
ALLOWED_REASONS = frozenset({
    "authentication_age",
    "replay_pattern",
    "rate_limit_pattern",
    "suspicious_authenticated_behavior",
    "operator_review",
})


class RectificationQueue:
    def __init__(self, path: str = ".zdx/rectification_queue.json"):
        self.path = Path(path)
        self.store = StateStore(self.path, "rectification-queue")
        self._state = self.store.load({"version": 1, "requests": {}})

    def _save(self) -> None:
        self.store.save(self._state)

    def pending(self) -> list[dict]:
        return sorted(
            (dict(item) for item in self._state.get("requests", {}).values()
             if item.get("status") == "pending"),
            key=lambda item: float(item.get("requested_at", 0.0)),
        )

    def pending_count(self) -> int:
        return len(self.pending())

    def has_pending(self, target_node_id: str, reason_code: str | None = None) -> bool:
        target = str(target_node_id)
        return any(
            item.get("target_node_id") == target
            and (reason_code is None or item.get("reason_code") == reason_code)
            for item in self.pending()
        )

    def request(
        self,
        *,
        reporter_node_id: str,
        target_node_id: str,
        reason_code: str,
        detail: str = "",
        evidence_digest: str = "",
        auth_age_seconds: Optional[float] = None,
        now: Optional[float] = None,
    ) -> dict:
        current = time.time() if now is None else float(now)
        reporter = str(reporter_node_id).strip()
        target = str(target_node_id).strip()
        reason = str(reason_code).strip()
        detail = str(detail).replace("\r", " ").replace("\n", " ").strip()[:MAX_DETAIL]
        digest = str(evidence_digest).lower().strip()

        if not reporter or not target:
            raise ValueError("rectification request requires reporter and target node IDs")
        if reason not in ALLOWED_REASONS:
            raise ValueError("unsupported rectification reason")
        if digest and (len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest)):
            raise ValueError("evidence_digest must be a SHA-256 hex digest")

        requests = self._state.setdefault("requests", {})
        pending = [item for item in requests.values() if item.get("status") == "pending"]
        if len(pending) >= MAX_PENDING:
            raise ValueError("rectification queue is full")

        # Coalesce the same reporter/target/reason instead of amplifying suspicion
        # through repeated submissions.
        for item in pending:
            if (
                item.get("reporter_node_id") == reporter
                and item.get("target_node_id") == target
                and item.get("reason_code") == reason
            ):
                if current - float(item.get("last_requested_at", item.get("requested_at", 0.0))) < COALESCE_WRITE_INTERVAL_SECONDS:
                    return dict(item)
                item["last_requested_at"] = current
                item["repeat_count"] = int(item.get("repeat_count", 1)) + 1
                if detail:
                    item["detail"] = detail
                if digest:
                    item["evidence_digest"] = digest
                if auth_age_seconds is not None:
                    item["auth_age_seconds"] = max(0.0, float(auth_age_seconds))
                self._save()
                return dict(item)

        recent = sum(
            1 for item in requests.values()
            if item.get("reporter_node_id") == reporter
            and current - float(item.get("requested_at", 0.0)) <= REPORTER_WINDOW_SECONDS
        )
        if recent >= MAX_REPORTS_PER_WINDOW:
            raise ValueError("rectification reporter rate limit exceeded")

        request_id = str(uuid.uuid4())
        record = {
            "request_id": request_id,
            "reporter_node_id": reporter,
            "target_node_id": target,
            "reason_code": reason,
            "detail": detail,
            "evidence_digest": digest,
            "requested_at": current,
            "last_requested_at": current,
            "repeat_count": 1,
            "status": "pending",
        }
        if auth_age_seconds is not None:
            record["auth_age_seconds"] = max(0.0, float(auth_age_seconds))
        record["request_sha256"] = hashlib.sha256(
            ("|".join([
                request_id, reporter, target, reason, digest, f"{current:.6f}"
            ])).encode("utf-8")
        ).hexdigest()
        requests[request_id] = record
        self._save()
        return dict(record)

    def resolve(self, request_id: str, resolution: str, *, actor_node_id: str) -> dict:
        item = self._state.get("requests", {}).get(str(request_id))
        if item is None:
            raise KeyError("unknown rectification request")
        if item.get("status") != "pending":
            return dict(item)
        item["status"] = "resolved"
        item["resolution"] = str(resolution).replace("\r", " ").replace("\n", " ").strip()[:MAX_DETAIL]
        item["resolved_by"] = str(actor_node_id)
        item["resolved_at"] = time.time()
        self._save()
        return dict(item)
