"""Unit-level proof that the approval gate blocks the write itself.

The mock transport counts HTTP verbs, so "nothing was written" is asserted
against observed traffic rather than against the return value's own claim.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from workflow_runner.approvals import ApprovalStore
from workflow_runner.audit import AuditLog
from workflow_runner.client import EnterpriseApiClient
from workflow_runner.executor import apply_incident_action

RUNBOOK: dict[str, Any] = {
    "incident_id": "INC-2026-0042",
    "runbook_id": "RB-PAY-GATEWAY-01",
    "steps": [
        {"step_id": "RB-PAY-GATEWAY-01-S1", "order": 1, "action": "Confirm monitors"},
        {"step_id": "RB-PAY-GATEWAY-01-S2", "order": 2, "action": "Scale replicas"},
    ],
}


class RecordingApi:
    """Minimal stand-in for the enterprise API that records every request."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        self.records: dict[str, dict[str, Any]] = {}

    @property
    def posts(self) -> list[tuple[str, str]]:
        return [r for r in self.requests if r[0] == "POST"]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path))
        headers = {"Content-Type": "application/json", "X-Correlation-ID": "corr-exec"}
        if request.url.path.endswith("/runbook"):
            return httpx.Response(200, content=json.dumps(RUNBOOK).encode(), headers=headers)
        if request.method == "POST":
            key = request.headers["Idempotency-Key"]
            replayed = key in self.records
            if not replayed:
                self.records[key] = {"record_id": f"ACT-{len(self.records) + 1}", "idempotency_key": key}
            return httpx.Response(
                200 if replayed else 201,
                content=json.dumps(
                    {
                        "replayed": replayed,
                        "record": self.records[key],
                        "total_actions_for_incident": len(self.records),
                    }
                ).encode(),
                headers=headers,
            )
        return httpx.Response(404, content=b"{}", headers=headers)


@pytest.fixture
def api() -> RecordingApi:
    return RecordingApi()


def _client(api: RecordingApi) -> EnterpriseApiClient:
    return EnterpriseApiClient(
        base_url="http://enterprise-api:8080",
        token="lab-write-token",
        transport=httpx.MockTransport(api.handler),
    )


def _stores(tmp_path: Path) -> tuple[ApprovalStore, AuditLog]:
    return (
        ApprovalStore(path=tmp_path / "approvals.json"),
        AuditLog(path=tmp_path / "audit.jsonl", run_id="executor-test"),
    )


def test_missing_approval_issues_a_token_and_writes_nothing(api: RecordingApi, tmp_path: Path) -> None:
    approvals, audit = _stores(tmp_path)
    result = apply_incident_action(
        _client(api),
        incident_id="INC-2026-0042",
        action_id="RB-PAY-GATEWAY-01-S2",
        approvals=approvals,
        audit=audit,
    )
    assert result["status"] == "pending_approval"
    assert result["side_effect"] is None
    assert result["approval_token"].startswith("apv_")
    assert api.posts == [], "the gate must block before any write request is sent"
    assert [e["event"] for e in audit.read_events()] == ["approval_requested"]


def test_forged_token_writes_nothing(api: RecordingApi, tmp_path: Path) -> None:
    approvals, audit = _stores(tmp_path)
    result = apply_incident_action(
        _client(api),
        incident_id="INC-2026-0042",
        action_id="RB-PAY-GATEWAY-01-S2",
        approval_token="apv_not_a_real_token",
        approvals=approvals,
        audit=audit,
    )
    assert result["status"] == "approval_rejected"
    assert result["reason"] == "unknown_approval_token"
    assert api.posts == []


def test_unknown_action_id_writes_nothing(api: RecordingApi, tmp_path: Path) -> None:
    approvals, audit = _stores(tmp_path)
    result = apply_incident_action(
        _client(api),
        incident_id="INC-2026-0042",
        action_id="RB-NOT-A-STEP",
        approvals=approvals,
        audit=audit,
    )
    assert result["status"] == "approval_rejected"
    assert result["reason"] == "unknown_action_id"
    assert api.posts == []


def test_token_bound_to_a_different_incident_is_refused(api: RecordingApi, tmp_path: Path) -> None:
    approvals, audit = _stores(tmp_path)
    approval = approvals.request("INC-OTHER", "RB-PAY-GATEWAY-01-S2")
    result = apply_incident_action(
        _client(api),
        incident_id="INC-2026-0042",
        action_id="RB-PAY-GATEWAY-01-S2",
        approval_token=approval.approval_token,
        approvals=approvals,
        audit=audit,
    )
    assert result["reason"] == "approval_token_bound_to_different_incident"
    assert api.posts == []


def test_approved_call_writes_once_and_replays_thereafter(api: RecordingApi, tmp_path: Path) -> None:
    approvals, audit = _stores(tmp_path)
    pending = apply_incident_action(
        _client(api),
        incident_id="INC-2026-0042",
        action_id="RB-PAY-GATEWAY-01-S2",
        approvals=approvals,
        audit=audit,
    )
    token = pending["approval_token"]

    applied = apply_incident_action(
        _client(api),
        incident_id="INC-2026-0042",
        action_id="RB-PAY-GATEWAY-01-S2",
        approval_token=token,
        approvals=approvals,
        audit=audit,
    )
    replayed = apply_incident_action(
        _client(api),
        incident_id="INC-2026-0042",
        action_id="RB-PAY-GATEWAY-01-S2",
        approval_token=token,
        approvals=approvals,
        audit=audit,
    )

    assert applied["status"] == "applied"
    assert replayed["status"] == "replayed"
    assert len(api.posts) == 2, "two attempts were made"
    assert len(api.records) == 1, "but only one side effect exists"
    assert applied["idempotency_key"] == replayed["idempotency_key"]

    kinds = [event["event"] for event in audit.read_events()]
    assert kinds.count("mutation_committed") == 1
    assert kinds.count("mutation_replayed") == 1
