"""Unit-level proof that the approval gate blocks the write itself.

The mock transport counts HTTP verbs, so "nothing was written" is asserted
against observed traffic rather than against the return value's own claim.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
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


def _grant(approvals: ApprovalStore, approval_id: str, actor: str = "operator@example.com") -> str:
    grant, rejection = approvals.approve(approval_id, actor)
    assert rejection is None
    assert grant is not None
    return grant.approval_capability


def test_missing_approval_issues_only_an_id_and_writes_nothing(
    api: RecordingApi, tmp_path: Path
) -> None:
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
    assert result["approval_id"].startswith("apr_")
    assert "approval_token" not in result
    assert "approval_capability" not in result
    assert "idempotency_key" not in result
    assert api.posts == [], "the gate must block before any write request is sent"
    assert [e["event"] for e in audit.read_events()] == ["approval_requested"]


def test_forged_capability_writes_nothing(api: RecordingApi, tmp_path: Path) -> None:
    approvals, audit = _stores(tmp_path)
    result = apply_incident_action(
        _client(api),
        incident_id="INC-2026-0042",
        action_id="RB-PAY-GATEWAY-01-S2",
        approval_capability="cap_not_a_real_capability",
        approvals=approvals,
        audit=audit,
    )
    assert result["status"] == "approval_rejected"
    assert result["reason"] == "unknown_approval_capability"
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


def test_capability_bound_to_a_different_incident_is_refused(
    api: RecordingApi, tmp_path: Path
) -> None:
    approvals, audit = _stores(tmp_path)
    approval = approvals.request("INC-OTHER", "RB-PAY-GATEWAY-01-S2")
    capability = _grant(approvals, approval.approval_id)
    result = apply_incident_action(
        _client(api),
        incident_id="INC-2026-0042",
        action_id="RB-PAY-GATEWAY-01-S2",
        approval_capability=capability,
        approvals=approvals,
        audit=audit,
    )
    assert result["reason"] == "approval_capability_bound_to_different_incident"
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
    capability = _grant(approvals, pending["approval_id"])

    applied = apply_incident_action(
        _client(api),
        incident_id="INC-2026-0042",
        action_id="RB-PAY-GATEWAY-01-S2",
        approval_capability=capability,
        approvals=approvals,
        audit=audit,
    )
    replayed = apply_incident_action(
        _client(api),
        incident_id="INC-2026-0042",
        action_id="RB-PAY-GATEWAY-01-S2",
        approval_capability=capability,
        approvals=approvals,
        audit=audit,
    )

    assert applied["status"] == "applied"
    assert approvals.get(pending["approval_id"]).status == "applied"
    assert replayed["status"] == "approval_rejected"
    assert replayed["reason"] == "approval_already_applied"
    assert len(api.posts) == 1, "the terminal capability cannot dispatch again"
    assert len(api.records) == 1, "but only one side effect exists"

    kinds = [event["event"] for event in audit.read_events()]
    assert kinds.count("mutation_committed") == 1
    assert kinds.count("mutation_replayed") == 0


def test_pending_request_cannot_be_used_as_a_capability(
    api: RecordingApi, tmp_path: Path
) -> None:
    approvals, audit = _stores(tmp_path)
    pending = apply_incident_action(
        _client(api),
        incident_id="INC-2026-0042",
        action_id="RB-PAY-GATEWAY-01-S2",
        approvals=approvals,
        audit=audit,
    )
    result = apply_incident_action(
        _client(api),
        incident_id="INC-2026-0042",
        action_id="RB-PAY-GATEWAY-01-S2",
        approval_capability=pending["approval_id"],
        approvals=approvals,
        audit=audit,
    )
    assert result["reason"] == "unknown_approval_capability"
    assert api.posts == []


def test_expired_capability_is_terminal_and_writes_nothing(
    api: RecordingApi, tmp_path: Path
) -> None:
    approvals = ApprovalStore(path=tmp_path / "approvals.json", ttl_seconds=30)
    audit = AuditLog(path=tmp_path / "audit.jsonl", run_id="expiry-test")
    requested_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    request = approvals.request(
        "INC-2026-0042", "RB-PAY-GATEWAY-01-S2", now=requested_at
    )
    grant, rejection = approvals.approve(
        request.approval_id,
        "operator@example.com",
        now=requested_at + timedelta(seconds=5),
    )
    assert rejection is None and grant is not None

    result = apply_incident_action(
        _client(api),
        incident_id="INC-2026-0042",
        action_id="RB-PAY-GATEWAY-01-S2",
        approval_capability=grant.approval_capability,
        approvals=approvals,
        audit=audit,
    )
    assert result["status"] == "approval_rejected"
    assert result["reason"] == "approval_expired"
    assert approvals.get(request.approval_id).status == "expired"
    assert api.posts == []


def test_operator_identity_is_recorded_and_plaintext_capability_is_not(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "approvals.json"
    approvals = ApprovalStore(path=store_path)
    request = approvals.request("INC-2026-0042", "RB-PAY-GATEWAY-01-S2")
    capability = _grant(approvals, request.approval_id, "alice@example.com")
    persisted = store_path.read_text(encoding="utf-8")
    saved = approvals.get(request.approval_id)

    assert saved is not None
    assert saved.status == "approved"
    assert saved.approved_by == "alice@example.com"
    assert saved.capability_hash
    assert capability not in persisted


def test_expired_pending_request_cannot_be_approved(tmp_path: Path) -> None:
    approvals = ApprovalStore(path=tmp_path / "approvals.json", ttl_seconds=10)
    requested_at = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
    request = approvals.request(
        "INC-2026-0042", "RB-PAY-GATEWAY-01-S2", now=requested_at
    )
    grant, rejection = approvals.approve(
        request.approval_id,
        "operator@example.com",
        now=requested_at + timedelta(seconds=11),
    )
    assert grant is None
    assert rejection == "approval_expired"
    assert approvals.get(request.approval_id).status == "expired"
