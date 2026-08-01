from __future__ import annotations

import json
from pathlib import Path

from workflow_runner.audit import AuditLog


def test_events_are_append_only(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    audit = AuditLog(path=path, run_id="run-1")
    audit.append("run_started", correlation_id="c1", outcome="ok")
    first_bytes = path.read_bytes()

    audit.append("tool_invoked", correlation_id="c2", tool="propose_incident_plan")
    audit.append("run_finished", correlation_id="c3", outcome="ok")
    after_bytes = path.read_bytes()

    # Earlier bytes are untouched: the file only ever grew at the end.
    assert after_bytes.startswith(first_bytes)
    assert len(after_bytes) > len(first_bytes)


def test_sequence_numbers_and_run_scoping(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    AuditLog(path=path, run_id="run-a").append("run_started")
    AuditLog(path=path, run_id="run-b").append("run_started")
    AuditLog(path=path, run_id="run-a").append("run_finished")

    audit = AuditLog(path=path, run_id="run-a")
    assert [event["seq"] for event in audit.read_events()] == [1, 2, 3]
    assert [event["event"] for event in audit.events_for_run("run-a")] == [
        "run_started",
        "run_finished",
    ]


def test_every_line_is_standalone_json(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    audit = AuditLog(path=path, run_id="run-1")
    audit.append("tool_invoked", correlation_id="c1", tool="apply_incident_plan", nested={"a": 1})
    for line in path.read_text(encoding="utf-8").splitlines():
        parsed = json.loads(line)
        assert {"ts", "run_id", "event", "actor", "seq"} <= set(parsed)
