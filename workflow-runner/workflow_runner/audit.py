"""Append-only structured audit log.

Every event is one JSON object on one line. The file is only ever opened in
append mode, so earlier bytes cannot be rewritten by this module; a test asserts
that property against a real file.

Event vocabulary (`event` field):
    tool_invoked          an MCP tool was called
    approval_requested    a mutation was refused and an approval token was minted
    approval_granted      a caller presented a valid approval token
    approval_rejected     a caller presented a missing/invalid/mismatched token
    mutation_attempted    the write request left the workflow layer
    mutation_committed    the write created a new side effect
    mutation_replayed     the write matched an existing idempotency key; no new side effect
    mutation_failed       the write attempt errored; resume information recorded
    run_started/run_finished  demo-arc bookends
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_AUDIT_PATH = ".audit/audit.jsonl"

_LOCK = threading.Lock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditLog:
    def __init__(self, path: str | os.PathLike[str] | None = None, run_id: str | None = None):
        self.path = Path(path or os.environ.get("AUDIT_LOG_PATH", DEFAULT_AUDIT_PATH))
        self.run_id = run_id or os.environ.get("AUDIT_RUN_ID", "unspecified-run")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(
        self,
        event: str,
        *,
        correlation_id: str | None = None,
        actor: str = "workflow-runner",
        **fields: Any,
    ) -> dict[str, Any]:
        record: dict[str, Any] = {
            "ts": _utc_now(),
            "run_id": self.run_id,
            "event": event,
            "actor": actor,
            "correlation_id": correlation_id,
        }
        record.update(fields)
        with _LOCK:
            record["seq"] = self._next_seq()
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, sort_keys=True) + "\n")
        return record

    def _next_seq(self) -> int:
        if not self.path.exists():
            return 1
        with self.path.open("r", encoding="utf-8") as fh:
            return sum(1 for line in fh if line.strip()) + 1

    def read_events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events

    def events_for_run(self, run_id: str | None = None) -> list[dict[str, Any]]:
        target = run_id or self.run_id
        return [e for e in self.read_events() if e.get("run_id") == target]
