"""In-process side-effect store for the fixture enterprise API.

This is the only thing in the lab that can be "duplicated" by a retry, so it is
the object every idempotency proof asserts against. Records are append-only:
`commit` never mutates an existing record, and a repeated idempotency key
returns the original record with `created=False`.

Optional file-backed persistence (`ACTION_STORE_PATH` / path=) lets records
survive process and container restarts. With no path the store stays in memory.

File-backed mode uses a separate stable lock file (`<data-path>.lock`) and
Linux/POSIX `fcntl.flock` around every load-modify-replace. The JSON data file
itself is not locked because atomic `os.replace()` swaps its inode. This is a
single-host file-lock demonstration, not a distributed or production datastore.
"""

from __future__ import annotations

import fcntl
import json
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


_REQUIRED_ACTION_STRING_FIELDS = frozenset(
    {
        "record_id",
        "incident_id",
        "action_id",
        "idempotency_key",
        "correlation_id",
        "applied_at",
    }
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_action_record(raw: Any, *, path: Path) -> dict[str, Any]:
    """Reject malformed durable records before publishing them in memory."""
    if not isinstance(raw, dict):
        raise ValueError(f"ACTION_STORE persistence file has invalid record: {path}")

    required_fields = _REQUIRED_ACTION_STRING_FIELDS | {"sequence"}
    missing = required_fields - raw.keys()
    if missing:
        raise ValueError(
            f"ACTION_STORE persistence record missing required fields {sorted(missing)}: {path}"
        )

    for field_name in _REQUIRED_ACTION_STRING_FIELDS:
        value = raw[field_name]
        if not isinstance(value, str) or not value:
            raise ValueError(
                f"ACTION_STORE persistence record has invalid {field_name}: {path}"
            )

    try:
        datetime.fromisoformat(raw["applied_at"])
    except ValueError as exc:
        raise ValueError(
            f"ACTION_STORE persistence record has invalid applied_at: {path}"
        ) from exc

    sequence = raw["sequence"]
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise ValueError(f"ACTION_STORE persistence record has invalid sequence: {path}")

    note = raw.get("note")
    if note is not None and not isinstance(note, str):
        raise ValueError(f"ACTION_STORE persistence record has invalid note: {path}")

    return raw


class ActionStore:
    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self._lock = threading.Lock()
        self._records: list[dict[str, Any]] = []
        self._by_key: dict[str, dict[str, Any]] = {}
        self._path: Path | None = Path(path) if path else None
        if self._path is not None:
            with self._lock:
                with self._os_lock():
                    self._load_unlocked()

    @property
    def _lock_path(self) -> Path | None:
        if self._path is None:
            return None
        return Path(str(self._path) + ".lock")

    @contextmanager
    def _os_lock(self) -> Iterator[None]:
        """Exclusive advisory lock on the stable lock-file inode (Linux/POSIX)."""
        lock_path = self._lock_path
        if lock_path is None:
            yield
            return
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.touch(exist_ok=True)
        with lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _load_unlocked(self) -> None:
        assert self._path is not None
        if not self._path.exists():
            self._records = []
            self._by_key = {}
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise ValueError(
                f"ACTION_STORE persistence file is unreadable or corrupt: {self._path}"
            ) from exc
        if not isinstance(raw, dict) or "records" not in raw:
            raise ValueError(
                f"ACTION_STORE persistence file has invalid shape: {self._path}"
            )
        records = raw["records"]
        if not isinstance(records, list):
            raise ValueError(
                f"ACTION_STORE persistence file has invalid records list: {self._path}"
            )
        by_key: dict[str, dict[str, Any]] = {}
        normalized: list[dict[str, Any]] = []
        for raw_item in records:
            item = _validate_action_record(raw_item, path=self._path)
            key = item["idempotency_key"]
            if key in by_key:
                raise ValueError(
                    f"ACTION_STORE persistence file has duplicate idempotency key: {self._path}"
                )
            by_key[key] = item
            normalized.append(item)
        self._records = normalized
        self._by_key = by_key

    def _persist_records_unlocked(self, records: list[dict[str, Any]]) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"records": records}
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self._path)

    def _reload_if_file_backed_unlocked(self) -> None:
        if self._path is not None:
            self._load_unlocked()

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
            with self._os_lock():
                self._reload_if_file_backed_unlocked()
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
                # Persist the prospective state before publishing it in memory. If
                # the durable write fails, callers receive an error and a same-
                # process retry cannot observe a phantom record that would vanish
                # after restart.
                prospective_records = [*self._records, record]
                self._persist_records_unlocked(prospective_records)
                self._records = prospective_records
                self._by_key[idempotency_key] = record
                return record, True

    def get_by_key(self, idempotency_key: str) -> dict[str, Any] | None:
        with self._lock:
            with self._os_lock():
                self._reload_if_file_backed_unlocked()
                return self._by_key.get(idempotency_key)

    def list_for_incident(self, incident_id: str) -> list[dict[str, Any]]:
        with self._lock:
            with self._os_lock():
                self._reload_if_file_backed_unlocked()
                return [r for r in self._records if r["incident_id"] == incident_id]

    def all_records(self) -> list[dict[str, Any]]:
        with self._lock:
            with self._os_lock():
                self._reload_if_file_backed_unlocked()
                return list(self._records)

    def reset(self) -> int:
        """Clear the fixture store. Lab-only affordance for deterministic demos."""
        with self._lock:
            with self._os_lock():
                self._reload_if_file_backed_unlocked()
                count = len(self._records)
                # As with commit(), publish the state transition in memory only
                # after the durable representation is safely replaced.
                self._persist_records_unlocked([])
                self._records = []
                self._by_key = {}
                return count


def _path_from_settings() -> str | None:
    # Local import keeps store importable without requiring settings during unit tests.
    from app.config import settings

    raw = settings.action_store_path
    if raw is None:
        return None
    stripped = str(raw).strip()
    return stripped or None


ACTION_STORE = ActionStore(path=_path_from_settings())
