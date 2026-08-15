"""Unit-level proof that the approval gate blocks the write itself.

The mock transport counts HTTP verbs, so "nothing was written" is asserted
against observed traffic rather than against the return value's own claim.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from workflow_runner.approvals import ApprovalStore
from workflow_runner.audit import AuditLog
from workflow_runner.client import EnterpriseApiClient
from workflow_runner.executor import apply_incident_action
from workflow_runner.tracing import configure_tracing, flush_tracing, reset_tracing_for_tests, shutdown_tracing

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
        headers = {
            "Content-Type": "application/json",
            "X-Correlation-ID": "44444444-4444-4444-8444-444444444444",
        }
        if request.url.path.endswith("/runbook"):
            return httpx.Response(200, content=json.dumps(RUNBOOK).encode(), headers=headers)
        if request.method == "POST":
            key = request.headers["Idempotency-Key"]
            replayed = key in self.records
            if not replayed:
                self.records[key] = {"record_id": f"ACT-{len(self.records) + 1}", "idempotency_key": key}
            if "error_after_commit" in str(request.url.query):
                return httpx.Response(
                    500,
                    content=json.dumps({"detail": "Injected upstream failure after commit"}).encode(),
                    headers=headers,
                )
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


def test_two_distinct_approvals_for_same_action_create_only_one_record(
    api: RecordingApi, tmp_path: Path
) -> None:
    approvals, audit = _stores(tmp_path)
    first_pending = apply_incident_action(
        _client(api),
        "INC-2026-0042",
        "RB-PAY-GATEWAY-01-S2",
        approvals=approvals,
        audit=audit,
    )
    first_capability = _grant(approvals, first_pending["approval_id"])

    second_pending = apply_incident_action(
        _client(api),
        "INC-2026-0042",
        "RB-PAY-GATEWAY-01-S2",
        approvals=approvals,
        audit=audit,
    )
    second_capability = _grant(approvals, second_pending["approval_id"])

    first = apply_incident_action(
        _client(api),
        "INC-2026-0042",
        "RB-PAY-GATEWAY-01-S2",
        approval_capability=first_capability,
        approvals=approvals,
        audit=audit,
    )
    assert first["status"] == "applied"

    second = apply_incident_action(
        _client(api),
        "INC-2026-0042",
        "RB-PAY-GATEWAY-01-S2",
        approval_capability=second_capability,
        approvals=approvals,
        audit=audit,
    )

    assert second["status"] == "approval_rejected"
    assert second["reason"] == "action_already_applied"
    assert len(api.posts) == 1
    assert len(api.records) == 1


def test_legacy_approval_uses_current_action_scoped_key_at_dispatch(
    api: RecordingApi, tmp_path: Path
) -> None:
    approvals, audit = _stores(tmp_path)
    legacy = approvals.request("INC-2026-0042", "RB-PAY-GATEWAY-01-S2")
    persisted = json.loads(approvals.path.read_text(encoding="utf-8"))
    persisted[legacy.approval_id]["idempotency_key"] = "idem-legacy-random-key"
    approvals.path.write_text(json.dumps(persisted), encoding="utf-8")
    capability = _grant(approvals, legacy.approval_id)
    current = approvals.request("INC-2026-0042", "RB-PAY-GATEWAY-01-S2")

    applied = apply_incident_action(
        _client(api),
        "INC-2026-0042",
        "RB-PAY-GATEWAY-01-S2",
        approval_capability=capability,
        approvals=approvals,
        audit=audit,
    )

    assert applied["status"] == "applied"
    assert applied["side_effect"]["idempotency_key"] == current.idempotency_key
    assert applied["side_effect"]["idempotency_key"] != "idem-legacy-random-key"


def test_concurrent_distinct_approvals_converge_on_one_side_effect(
    api: RecordingApi, tmp_path: Path
) -> None:
    approvals, audit = _stores(tmp_path)
    first = approvals.request("INC-2026-0042", "RB-PAY-GATEWAY-01-S2")
    second = approvals.request("INC-2026-0042", "RB-PAY-GATEWAY-01-S2")
    first_capability = _grant(approvals, first.approval_id)
    second_capability = _grant(approvals, second.approval_id)
    post_barrier = threading.Barrier(2)
    original_handler = api.handler

    def concurrent_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            post_barrier.wait(timeout=10)
        return original_handler(request)

    def apply(capability: str) -> dict[str, Any]:
        client = EnterpriseApiClient(
            base_url="http://enterprise-api:8080",
            token="lab-write-token",
            transport=httpx.MockTransport(concurrent_handler),
        )
        return apply_incident_action(
            client,
            "INC-2026-0042",
            "RB-PAY-GATEWAY-01-S2",
            approval_capability=capability,
            approvals=approvals,
            audit=audit,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(apply, [first_capability, second_capability]))

    assert sorted(result["status"] for result in results) == ["applied", "replayed"]
    assert len(api.posts) == 2
    assert len(api.records) == 1
    assert len({result["side_effect"]["record_id"] for result in results}) == 1


def test_upstream_existing_action_conflict_terminates_capability(
    tmp_path: Path,
) -> None:
    approvals, audit = _stores(tmp_path)
    approval = approvals.request("INC-2026-0042", "RB-PAY-GATEWAY-01-S2")
    capability = _grant(approvals, approval.approval_id)

    def handler(request: httpx.Request) -> httpx.Response:
        headers = {
            "Content-Type": "application/json",
            "X-Correlation-ID": "44444444-4444-4444-8444-444444444444",
        }
        if request.url.path.endswith("/runbook"):
            return httpx.Response(200, content=json.dumps(RUNBOOK).encode(), headers=headers)
        return httpx.Response(
            409,
            content=json.dumps(
                {
                    "detail": {
                        "code": "incident_action_already_applied",
                        "existing_record_id": "ACT-direct-existing",
                    }
                }
            ).encode(),
            headers=headers,
        )

    client = EnterpriseApiClient(
        base_url="http://enterprise-api:8080",
        token="lab-write-token",
        transport=httpx.MockTransport(handler),
    )
    result = apply_incident_action(
        client,
        "INC-2026-0042",
        "RB-PAY-GATEWAY-01-S2",
        approval_capability=capability,
        approvals=approvals,
        audit=audit,
    )

    assert result["status"] == "approval_rejected"
    assert result["reason"] == "action_already_applied"
    assert result["existing_record_id"] == "ACT-direct-existing"
    assert "resume" not in result
    persisted = {item.approval_id: item for item in approvals.all_requests()}
    assert persisted[approval.approval_id].status == "applied"
    assert persisted[approval.approval_id].applied_record_id == "ACT-direct-existing"


def test_upstream_key_binding_conflict_is_not_marked_resumable(tmp_path: Path) -> None:
    approvals, audit = _stores(tmp_path)
    approval = approvals.request("INC-2026-0042", "RB-PAY-GATEWAY-01-S2")
    capability = _grant(approvals, approval.approval_id)

    def handler(request: httpx.Request) -> httpx.Response:
        headers = {
            "Content-Type": "application/json",
            "X-Correlation-ID": "44444444-4444-4444-8444-444444444444",
        }
        if request.url.path.endswith("/runbook"):
            return httpx.Response(200, content=json.dumps(RUNBOOK).encode(), headers=headers)
        return httpx.Response(
            409,
            content=json.dumps(
                {
                    "detail": {
                        "code": "idempotency_key_binding_conflict",
                        "existing_record_id": "ACT-other-action",
                    }
                }
            ).encode(),
            headers=headers,
        )

    result = apply_incident_action(
        EnterpriseApiClient(
            base_url="http://enterprise-api:8080",
            token="lab-write-token",
            transport=httpx.MockTransport(handler),
        ),
        "INC-2026-0042",
        "RB-PAY-GATEWAY-01-S2",
        approval_capability=capability,
        approvals=approvals,
        audit=audit,
    )

    assert result["status"] == "error"
    assert result["error"]["code"] == "conflict"
    assert result["resumable"] is False
    assert "resume" not in result
    persisted = {item.approval_id: item for item in approvals.all_requests()}
    assert persisted[approval.approval_id].status == "approved"


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


def _internal_events(exporter: InMemorySpanExporter) -> list[list[str]]:
    flush_tracing()
    return [
        [event.name for event in span.events]
        for span in exporter.get_finished_spans()
        if span.kind == SpanKind.INTERNAL and span.name == "apply_incident_action"
    ]


def _span_blob(exporter: InMemorySpanExporter) -> str:
    flush_tracing()
    return "\n".join(span.to_json() for span in exporter.get_finished_spans())


@pytest.fixture
def span_exporter(monkeypatch: pytest.MonkeyPatch) -> InMemorySpanExporter:
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "otlp")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    reset_tracing_for_tests()
    exporter = InMemorySpanExporter()
    configure_tracing(exporter=exporter)
    yield exporter
    shutdown_tracing()
    reset_tracing_for_tests()


def test_pending_flow_emits_approval_requested_event(
    api: RecordingApi, tmp_path: Path, span_exporter: InMemorySpanExporter
) -> None:
    approvals, audit = _stores(tmp_path)
    apply_incident_action(
        _client(api),
        incident_id="INC-2026-0042",
        action_id="RB-PAY-GATEWAY-01-S2",
        note="secret-note-must-not-trace",
        approvals=approvals,
        audit=audit,
    )
    assert _internal_events(span_exporter) == [["approval.requested"]]
    blob = _span_blob(span_exporter)
    assert "secret-note-must-not-trace" not in blob
    assert "lab-write-token" not in blob
    assert "INC-2026-0042" not in blob
    assert "RB-PAY-GATEWAY-01-S2" not in blob
    assert "apr_" not in blob
    assert "idem-" not in blob


def test_rejected_flow_emits_approval_rejected_event(
    api: RecordingApi, tmp_path: Path, span_exporter: InMemorySpanExporter
) -> None:
    approvals, audit = _stores(tmp_path)
    apply_incident_action(
        _client(api),
        incident_id="INC-2026-0042",
        action_id="RB-PAY-GATEWAY-01-S2",
        approval_capability="cap_not_a_real_capability",
        approvals=approvals,
        audit=audit,
    )
    events = _internal_events(span_exporter)
    assert events == [["approval.rejected"]]
    blob = _span_blob(span_exporter)
    assert "cap_not_a_real_capability" not in blob
    assert "lab-write-token" not in blob


def test_postcommit_failure_then_replay_emits_bounded_events(
    api: RecordingApi, tmp_path: Path, span_exporter: InMemorySpanExporter
) -> None:
    approvals, audit = _stores(tmp_path)
    pending = apply_incident_action(
        _client(api),
        incident_id="INC-2026-0042",
        action_id="RB-PAY-GATEWAY-01-S2",
        note="resume-note-must-not-trace",
        approvals=approvals,
        audit=audit,
    )
    capability = _grant(approvals, pending["approval_id"])
    idempotency_key = approvals.get(pending["approval_id"]).idempotency_key

    failed = apply_incident_action(
        _client(api),
        incident_id="INC-2026-0042",
        action_id="RB-PAY-GATEWAY-01-S2",
        approval_capability=capability,
        note="resume-note-must-not-trace",
        inject="error_after_commit",
        approvals=approvals,
        audit=audit,
    )
    replayed = apply_incident_action(
        _client(api),
        incident_id="INC-2026-0042",
        action_id="RB-PAY-GATEWAY-01-S2",
        approval_capability=capability,
        note="resume-note-must-not-trace",
        approvals=approvals,
        audit=audit,
    )

    assert failed["status"] == "error"
    assert replayed["status"] == "replayed"
    assert len(api.records) == 1
    assert _internal_events(span_exporter) == [
        ["approval.requested"],
        ["approval.accepted", "mutation.dispatched", "mutation.failed_resumable"],
        ["approval.accepted", "mutation.dispatched", "mutation.replayed"],
    ]
    blob = _span_blob(span_exporter)
    assert capability not in blob
    assert idempotency_key not in blob
    assert pending["approval_id"] not in blob
    assert "resume-note-must-not-trace" not in blob
    assert "lab-write-token" not in blob
    assert "Injected upstream failure after commit" not in blob
    assert "idem-" not in blob
    assert "cap_" not in blob
    assert "apr_" not in blob
