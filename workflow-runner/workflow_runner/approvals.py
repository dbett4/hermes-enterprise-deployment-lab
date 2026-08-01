"""Runtime human-approval gate.

The gate is enforcing, not advisory. `ApprovalStore.request()` mints an
unguessable token and records it as `pending`; nothing is written to the
enterprise API on that first call. The write only happens when a caller comes
back with that exact token, bound to the same (incident_id, action_id). The
human step is supplying the token out of band.

The token is deliberately reusable until the mutation commits, because a failed
attempt must be resumable. Once committed, the idempotency key carried alongside
the approval prevents a second side effect.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_APPROVAL_STORE_PATH = ".audit/approvals.json"

_LOCK = threading.Lock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ApprovalRequest:
    approval_token: str
    incident_id: str
    action_id: str
    idempotency_key: str
    requested_at: str
    status: str = "pending"  # pending | applied
    correlation_id: str | None = None
    applied_record_id: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ApprovalRequest":
        return cls(**data)


class ApprovalStore:
    def __init__(self, path: str | os.PathLike[str] | None = None):
        self.path = Path(path or os.environ.get("APPROVAL_STORE_PATH", DEFAULT_APPROVAL_STORE_PATH))
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

    def request(
        self,
        incident_id: str,
        action_id: str,
        correlation_id: str | None = None,
    ) -> ApprovalRequest:
        approval = ApprovalRequest(
            approval_token=f"apv_{secrets.token_urlsafe(24)}",
            incident_id=incident_id,
            action_id=action_id,
            idempotency_key=f"idem-{uuid.uuid4()}",
            requested_at=_utc_now(),
            correlation_id=correlation_id,
        )
        with _LOCK:
            data = self._load()
            data[approval.approval_token] = approval.to_dict()
            self._save(data)
        return approval

    def get(self, approval_token: str) -> ApprovalRequest | None:
        data = self._load()
        raw = data.get(approval_token)
        return ApprovalRequest.from_dict(raw) if raw else None

    def validate(
        self, approval_token: str, incident_id: str, action_id: str
    ) -> tuple[ApprovalRequest | None, str | None]:
        """Return (approval, rejection_reason)."""
        approval = self.get(approval_token)
        if approval is None:
            return None, "unknown_approval_token"
        if approval.incident_id != incident_id:
            return None, "approval_token_bound_to_different_incident"
        if approval.action_id != action_id:
            return None, "approval_token_bound_to_different_action"
        return approval, None

    def record_attempt(self, approval_token: str, outcome: str, detail: dict[str, Any]) -> None:
        with _LOCK:
            data = self._load()
            raw = data.get(approval_token)
            if raw is None:
                return
            raw.setdefault("history", []).append(
                {"at": _utc_now(), "outcome": outcome, **detail}
            )
            data[approval_token] = raw
            self._save(data)

    def mark_applied(self, approval_token: str, record_id: str) -> None:
        with _LOCK:
            data = self._load()
            raw = data.get(approval_token)
            if raw is None:
                return
            raw["status"] = "applied"
            raw["applied_record_id"] = record_id
            data[approval_token] = raw
            self._save(data)

    def all_requests(self) -> list[ApprovalRequest]:
        return [ApprovalRequest.from_dict(v) for v in self._load().values()]
