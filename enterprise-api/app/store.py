"""In-process side-effect store for the fixture enterprise API.

This is the only thing in the lab that can be "duplicated" by a retry, so it is
the object every idempotency proof asserts against. Records are append-only:
`commit` never mutates an existing record, and a repeated idempotency key
returns the original record with `created=False`.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ActionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: list[dict[str, Any]] = []
        self._by_key: dict[str, dict[str, Any]] = {}

    def commit(
        self,
        *,
        incident_id: str,
        action_id: str,
        idempotency_key: str,
        correlation_id: str,
        note: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Return (record, created). `created` is False for an idempotent replay."""
        with self._lock:
            existing = self._by_key.get(idempotency_key)
            if existing is not None:
                return existing, False

            record: dict[str, Any] = {
                "record_id": f"ACT-{uuid.uuid4().hex[:12]}",
                "incident_id": incident_id,
                "action_id": action_id,
                "idempotency_key": idempotency_key,
                "correlation_id": correlation_id,
                "note": note,
                "applied_at": _utc_now(),
                "sequence": len(self._records) + 1,
            }
            self._records.append(record)
            self._by_key[idempotency_key] = record
            return record, True

    def get_by_key(self, idempotency_key: str) -> dict[str, Any] | None:
        with self._lock:
            return self._by_key.get(idempotency_key)

    def list_for_incident(self, incident_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [r for r in self._records if r["incident_id"] == incident_id]

    def all_records(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._records)

    def reset(self) -> int:
        """Clear the fixture store. Lab-only affordance for deterministic demos."""
        with self._lock:
            count = len(self._records)
            self._records.clear()
            self._by_key.clear()
            return count


ACTION_STORE = ActionStore()
