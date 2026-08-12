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

This is a portfolio-lab control backed by one local JSON file. Concurrent
writers on a single Linux host are serialized with a separate stable lock file
(``<data-path>.lock``) and POSIX ``fcntl.flock``. The JSON data file itself is
not locked because atomic replace swaps its inode. It demonstrates role
separation and lifecycle enforcement; it is not a distributed/production
identity, authorization, or datastore.
"""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import secrets
import threading
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

DEFAULT_APPROVAL_STORE_PATH = ".audit/approvals.json"
DEFAULT_APPROVAL_TTL_SECONDS = 900

_REQUIRED_APPROVAL_FIELDS = frozenset(
    {
        "approval_id",
        "incident_id",
        "action_id",
        "idempotency_key",
        "requested_at",
        "expires_at",
        "status",
    }
)
_ALLOWED_APPROVAL_STATUSES = frozenset({"pending", "approved", "applied", "expired"})
_OPTIONAL_STRING_APPROVAL_FIELDS = frozenset(
    {
        "correlation_id",
        "approved_at",
        "approved_by",
        "capability_hash",
        "applied_at",
        "applied_record_id",
    }
)

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


def _validate_approval_entry(approval_id: str, raw: Any, *, path: Path) -> dict[str, Any]:
    """Fail closed on malformed per-entry shapes before attribute access."""
    if not isinstance(raw, dict):
        raise ValueError(
            f"APPROVAL_STORE persistence entry {approval_id!r} has invalid shape: {path}"
        )
    missing = _REQUIRED_APPROVAL_FIELDS - raw.keys()
    if missing:
        raise ValueError(
            f"APPROVAL_STORE persistence entry {approval_id!r} missing required fields "
            f"{sorted(missing)}: {path}"
        )
    for field_name in _REQUIRED_APPROVAL_FIELDS:
        value = raw[field_name]
        if not isinstance(value, str) or not value:
            raise ValueError(
                f"APPROVAL_STORE persistence entry {approval_id!r} has invalid "
                f"{field_name}: {path}"
            )
    if raw["approval_id"] != approval_id:
        raise ValueError(
            f"APPROVAL_STORE persistence entry key {approval_id!r} does not match "
            f"approval_id: {path}"
        )
    if raw["status"] not in _ALLOWED_APPROVAL_STATUSES:
        raise ValueError(
            f"APPROVAL_STORE persistence entry {approval_id!r} has invalid status: {path}"
        )
    try:
        _parse(raw["requested_at"])
        _parse(raw["expires_at"])
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"APPROVAL_STORE persistence entry {approval_id!r} has invalid timestamp: {path}"
        ) from exc
    for field_name in _OPTIONAL_STRING_APPROVAL_FIELDS:
        value = raw.get(field_name)
        if value is not None and not isinstance(value, str):
            raise ValueError(
                f"APPROVAL_STORE persistence entry {approval_id!r} has invalid "
                f"{field_name}: {path}"
            )
    history = raw.get("history", [])
    if not isinstance(history, list) or not all(isinstance(item, dict) for item in history):
        raise ValueError(
            f"APPROVAL_STORE persistence entry {approval_id!r} has invalid history: {path}"
        )
    return raw


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
        self.lock_path = Path(str(self.path) + ".lock")

    @contextmanager
    def _os_lock(self) -> Iterator[None]:
        """Exclusive advisory lock on the stable lock-file inode (Linux/POSIX)."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.touch(exist_ok=True)
        with self.lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            # Fail closed: a corrupt approval store must not silently become empty
            # and mint fresh approvals over lost lifecycle state.
            raise ValueError(
                f"APPROVAL_STORE persistence file is unreadable or corrupt: {self.path}"
            ) from exc
        if not isinstance(raw, dict):
            raise ValueError(
                f"APPROVAL_STORE persistence file has invalid shape: {self.path}"
            )
        validated: dict[str, dict[str, Any]] = {}
        for approval_id, entry in raw.items():
            validated[str(approval_id)] = _validate_approval_entry(
                str(approval_id), entry, path=self.path
            )
        return validated

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    def _request_from_entry(self, raw: dict[str, Any]) -> ApprovalRequest:
        try:
            return ApprovalRequest.from_dict(raw)
        except (TypeError, ValueError, KeyError) as exc:
            raise ValueError(
                f"APPROVAL_STORE persistence entry could not be loaded: {self.path}"
            ) from exc

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
        updated = dict(raw)
        history = list(updated.get("history") or [])
        history.append({"at": _iso(now), "outcome": "expired"})
        updated["status"] = "expired"
        updated["history"] = history
        data[approval_id] = updated
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
            with self._os_lock():
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
            with self._os_lock():
                data = self._load()
                changed = self._expire_if_needed(data, approval_id, now=checked_at)
                raw = data.get(approval_id)
                if changed:
                    self._save(data)
        return self._request_from_entry(raw) if raw else None

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
            with self._os_lock():
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
                # Build prospective state, persist, then publish the grant. A failed
                # save must not return success for a lifecycle transition that never
                # landed on disk.
                updated = dict(raw)
                history = list(updated.get("history") or [])
                history.append(
                    {"at": _iso(approved_at), "outcome": "approved", "actor": approver}
                )
                updated["status"] = "approved"
                updated["approved_at"] = _iso(approved_at)
                updated["approved_by"] = approver
                updated["capability_hash"] = _capability_hash(capability)
                updated["history"] = history
                data[approval_id] = updated
                self._save(data)
                grant = ApprovalGrant(
                    approval_id=approval_id,
                    approval_capability=capability,
                    incident_id=updated["incident_id"],
                    action_id=updated["action_id"],
                    approved_by=approver,
                    approved_at=updated["approved_at"],
                    expires_at=updated["expires_at"],
                )

        return grant, None

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
            with self._os_lock():
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
                return self._request_from_entry(raw), None

    def record_attempt(
        self,
        approval_id: str,
        outcome: str,
        detail: dict[str, Any],
    ) -> None:
        with _LOCK:
            with self._os_lock():
                data = self._load()
                raw = data.get(approval_id)
                if raw is None:
                    return
                updated = dict(raw)
                history = list(updated.get("history") or [])
                history.append({"at": _iso(_utc_now()), "outcome": outcome, **detail})
                updated["history"] = history
                data[approval_id] = updated
                self._save(data)

    def mark_applied(self, approval_id: str, record_id: str) -> None:
        with _LOCK:
            with self._os_lock():
                data = self._load()
                raw = data.get(approval_id)
                if raw is None or raw.get("status") != "approved":
                    return
                applied_at = _utc_now()
                updated = dict(raw)
                history = list(updated.get("history") or [])
                history.append(
                    {"at": _iso(applied_at), "outcome": "applied", "record_id": record_id}
                )
                updated["status"] = "applied"
                updated["applied_at"] = _iso(applied_at)
                updated["applied_record_id"] = record_id
                updated["history"] = history
                data[approval_id] = updated
                self._save(data)

    def all_requests(self, *, now: datetime | None = None) -> list[ApprovalRequest]:
        checked_at = now or _utc_now()
        with _LOCK:
            with self._os_lock():
                data = self._load()
                changed = False
                for approval_id in list(data):
                    changed = self._expire_if_needed(data, approval_id, now=checked_at) or changed
                if changed:
                    self._save(data)
                return [self._request_from_entry(v) for v in data.values()]
