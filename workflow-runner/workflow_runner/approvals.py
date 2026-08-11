"""Separated, time-bound approval capability store.

The mutating caller can request approval, but cannot grant it. ``request()``
returns only an opaque approval ID. A separate operator path calls ``approve()``
with an approver identity and receives a capability exactly once; only its hash
is persisted. The capability is bound to one incident/action pair, expires, and
becomes terminal after a confirmed apply.

An approved capability intentionally remains usable after an ambiguous failed
attempt. The downstream idempotency key belongs to the approval, so a retry
after a post-commit 5xx safely observes a replay. Once that replay (or an
ordinary success) is observed, the approval is marked ``applied`` and cannot
dispatch another request.

This is a portfolio-lab control backed by one local JSON file. It demonstrates
role separation and lifecycle enforcement; it is not a production identity,
authorization, or concurrent datastore.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_APPROVAL_STORE_PATH = ".audit/approvals.json"
DEFAULT_APPROVAL_TTL_SECONDS = 900

_LOCK = threading.Lock()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _capability_hash(capability: str) -> str:
    return hashlib.sha256(capability.encode("utf-8")).hexdigest()


@dataclass
class ApprovalRequest:
    approval_id: str
    incident_id: str
    action_id: str
    idempotency_key: str
    requested_at: str
    expires_at: str
    status: str = "pending"  # pending | approved | applied | expired
    correlation_id: str | None = None
    approved_at: str | None = None
    approved_by: str | None = None
    capability_hash: str | None = None
    applied_at: str | None = None
    applied_record_id: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ApprovalRequest":
        return cls(**data)


@dataclass(frozen=True)
class ApprovalGrant:
    approval_id: str
    approval_capability: str
    incident_id: str
    action_id: str
    approved_by: str
    approved_at: str
    expires_at: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class ApprovalStore:
    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        ttl_seconds: int | None = None,
    ):
        self.path = Path(path or os.environ.get("APPROVAL_STORE_PATH", DEFAULT_APPROVAL_STORE_PATH))
        configured_ttl = os.environ.get("APPROVAL_TTL_SECONDS")
        self.ttl_seconds = int(
            ttl_seconds if ttl_seconds is not None else configured_ttl or DEFAULT_APPROVAL_TTL_SECONDS
        )
        if self.ttl_seconds <= 0:
            raise ValueError("approval TTL must be greater than zero")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    def _expire_if_needed(
        self,
        data: dict[str, dict[str, Any]],
        approval_id: str,
        *,
        now: datetime,
    ) -> bool:
        raw = data.get(approval_id)
        if raw is None or raw.get("status") not in {"pending", "approved"}:
            return False
        if now < _parse(raw["expires_at"]):
            return False
        raw["status"] = "expired"
        raw.setdefault("history", []).append({"at": _iso(now), "outcome": "expired"})
        data[approval_id] = raw
        return True

    def request(
        self,
        incident_id: str,
        action_id: str,
        correlation_id: str | None = None,
        *,
        now: datetime | None = None,
    ) -> ApprovalRequest:
        requested_at = now or _utc_now()
        approval = ApprovalRequest(
            approval_id=f"apr_{secrets.token_urlsafe(18)}",
            incident_id=incident_id,
            action_id=action_id,
            idempotency_key=f"idem-{uuid.uuid4()}",
            requested_at=_iso(requested_at),
            expires_at=_iso(requested_at + timedelta(seconds=self.ttl_seconds)),
            correlation_id=correlation_id,
            history=[{"at": _iso(requested_at), "outcome": "requested"}],
        )
        with _LOCK:
            data = self._load()
            data[approval.approval_id] = approval.to_dict()
            self._save(data)
        return approval

    def get(
        self,
        approval_id: str,
        *,
        now: datetime | None = None,
    ) -> ApprovalRequest | None:
        checked_at = now or _utc_now()
        with _LOCK:
            data = self._load()
            changed = self._expire_if_needed(data, approval_id, now=checked_at)
            raw = data.get(approval_id)
            if changed:
                self._save(data)
        return ApprovalRequest.from_dict(raw) if raw else None

    def approve(
        self,
        approval_id: str,
        approver_identity: str,
        *,
        now: datetime | None = None,
    ) -> tuple[ApprovalGrant | None, str | None]:
        """Grant one pending request and return its capability once.

        The plaintext capability is never written to disk. A repeated approve
        command cannot recover it and is deliberately rejected.
        """
        approver = approver_identity.strip()
        if not approver:
            return None, "approver_identity_required"
        approved_at = now or _utc_now()
        with _LOCK:
            data = self._load()
            changed = self._expire_if_needed(data, approval_id, now=approved_at)
            raw = data.get(approval_id)
            if raw is None:
                return None, "unknown_approval_id"
            if changed:
                self._save(data)
                return None, "approval_expired"
            if raw.get("status") != "pending":
                return None, f"approval_{raw.get('status', 'invalid')}"

            capability = f"cap_{secrets.token_urlsafe(32)}"
            raw["status"] = "approved"
            raw["approved_at"] = _iso(approved_at)
            raw["approved_by"] = approver
            raw["capability_hash"] = _capability_hash(capability)
            raw.setdefault("history", []).append(
                {"at": _iso(approved_at), "outcome": "approved", "actor": approver}
            )
            data[approval_id] = raw
            self._save(data)

        return (
            ApprovalGrant(
                approval_id=approval_id,
                approval_capability=capability,
                incident_id=raw["incident_id"],
                action_id=raw["action_id"],
                approved_by=approver,
                approved_at=raw["approved_at"],
                expires_at=raw["expires_at"],
            ),
            None,
        )

    def _find_by_capability(
        self, data: dict[str, dict[str, Any]], capability: str
    ) -> tuple[str | None, dict[str, Any] | None]:
        presented = _capability_hash(capability)
        for approval_id, raw in data.items():
            stored = raw.get("capability_hash")
            if stored and hmac.compare_digest(stored, presented):
                return approval_id, raw
        return None, None

    def validate(
        self,
        approval_capability: str,
        incident_id: str,
        action_id: str,
        *,
        now: datetime | None = None,
    ) -> tuple[ApprovalRequest | None, str | None]:
        """Return an approved, unexpired request or a stable rejection reason."""
        checked_at = now or _utc_now()
        with _LOCK:
            data = self._load()
            approval_id, raw = self._find_by_capability(data, approval_capability)
            if raw is None or approval_id is None:
                return None, "unknown_approval_capability"
            changed = self._expire_if_needed(data, approval_id, now=checked_at)
            raw = data[approval_id]
            if changed:
                self._save(data)
            if raw.get("status") == "expired":
                return None, "approval_expired"
            if raw.get("status") == "applied":
                return None, "approval_already_applied"
            if raw.get("status") != "approved":
                return None, "approval_not_granted"
            if raw["incident_id"] != incident_id:
                return None, "approval_capability_bound_to_different_incident"
            if raw["action_id"] != action_id:
                return None, "approval_capability_bound_to_different_action"
            return ApprovalRequest.from_dict(raw), None

    def record_attempt(
        self,
        approval_id: str,
        outcome: str,
        detail: dict[str, Any],
    ) -> None:
        with _LOCK:
            data = self._load()
            raw = data.get(approval_id)
            if raw is None:
                return
            raw.setdefault("history", []).append({"at": _iso(_utc_now()), "outcome": outcome, **detail})
            data[approval_id] = raw
            self._save(data)

    def mark_applied(self, approval_id: str, record_id: str) -> None:
        with _LOCK:
            data = self._load()
            raw = data.get(approval_id)
            if raw is None or raw.get("status") != "approved":
                return
            applied_at = _utc_now()
            raw["status"] = "applied"
            raw["applied_at"] = _iso(applied_at)
            raw["applied_record_id"] = record_id
            raw.setdefault("history", []).append(
                {"at": _iso(applied_at), "outcome": "applied", "record_id": record_id}
            )
            data[approval_id] = raw
            self._save(data)

    def all_requests(self, *, now: datetime | None = None) -> list[ApprovalRequest]:
        checked_at = now or _utc_now()
        with _LOCK:
            data = self._load()
            changed = False
            for approval_id in list(data):
                changed = self._expire_if_needed(data, approval_id, now=checked_at) or changed
            if changed:
                self._save(data)
        return [ApprovalRequest.from_dict(v) for v in data.values()]
