from __future__ import annotations

import json
import re
from typing import Any

import httpx
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from workflow_runner.client import EnterpriseApiClient
from workflow_runner.errors import WorkflowErrorCode
from workflow_runner.planner import run_incident_intake
from workflow_runner.tracing import configure_tracing, flush_tracing, reset_tracing_for_tests, shutdown_tracing

TRACEPARENT_RE = re.compile(r"^00-[0-9a-f]{32}-[0-9a-f]{16}-0[0-9a-f]$")


def _json_response(
    status_code: int,
    payload: dict[str, Any] | str,
    correlation_id: str = "33333333-3333-4333-8333-333333333333",
) -> httpx.Response:
    if isinstance(payload, dict):
        content = json.dumps(payload).encode()
    else:
        content = payload.encode()
    return httpx.Response(
        status_code=status_code,
        content=content,
        headers={"Content-Type": "application/json", "X-Correlation-ID": correlation_id},
        request=httpx.Request("GET", "http://test"),
    )


def test_success_path_builds_receipt() -> None:
    incident = {
        "incident_id": "INC-2026-0042",
        "severity": "high",
        "affected_service": "payment-gateway",
    }
    runbook = {
        "runbook_id": "RB-PAY-GATEWAY-01",
        "steps": [{"action": "Scale replicas", "approval_required": True}],
    }
    upstream_corr = "33333333-3333-4333-8333-333333333333"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/runbook"):
            return _json_response(200, runbook, correlation_id=upstream_corr)
        return _json_response(200, incident, correlation_id=upstream_corr)

    client = EnterpriseApiClient(
        "http://enterprise-api:8080",
        "lab-read-token",
        transport=httpx.MockTransport(handler),
    )
    receipt = run_incident_intake(client, "INC-2026-0042")

    assert receipt.outcome == "success"
    assert receipt.approval_required is True
    assert receipt.correlation_id == upstream_corr
    assert len(receipt.dependency_calls) == 2
    assert receipt.proposed_actions


def test_auth_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(401, {"detail": "unauthorized"})

    client = EnterpriseApiClient(
        "http://enterprise-api:8080",
        "bad-token",
        transport=httpx.MockTransport(handler),
    )
    receipt = run_incident_intake(client, "INC-2026-0042")

    assert receipt.outcome == "error"
    assert receipt.error is not None
    assert receipt.error["code"] == WorkflowErrorCode.AUTH_FAILURE.value


def test_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(404, {"detail": "not found"})

    client = EnterpriseApiClient(
        "http://enterprise-api:8080",
        "lab-read-token",
        transport=httpx.MockTransport(handler),
    )
    receipt = run_incident_intake(client, "INC-MISSING")

    assert receipt.outcome == "error"
    assert receipt.error["code"] == WorkflowErrorCode.NOT_FOUND.value


def test_upstream_5xx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(500, {"detail": "boom"})

    client = EnterpriseApiClient(
        "http://enterprise-api:8080",
        "lab-read-token",
        transport=httpx.MockTransport(handler),
    )
    receipt = run_incident_intake(client, "INC-2026-0042")

    assert receipt.outcome == "error"
    assert receipt.error["code"] == WorkflowErrorCode.UPSTREAM_5XX.value


def test_malformed_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"not-json",
            headers={"Content-Type": "text/plain"},
            request=httpx.Request("GET", "http://test"),
        )

    client = EnterpriseApiClient(
        "http://enterprise-api:8080",
        "lab-read-token",
        transport=httpx.MockTransport(handler),
    )
    receipt = run_incident_intake(client, "INC-2026-0042")

    assert receipt.outcome == "error"
    assert receipt.error["code"] == WorkflowErrorCode.MALFORMED_RESPONSE.value


def test_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=httpx.Request("GET", "http://test"))

    client = EnterpriseApiClient(
        "http://enterprise-api:8080",
        "lab-read-token",
        timeout_seconds=0.01,
        transport=httpx.MockTransport(handler),
    )
    receipt = run_incident_intake(client, "INC-2026-0042")

    assert receipt.outcome == "error"
    assert receipt.error["code"] == WorkflowErrorCode.TIMEOUT.value


def test_client_injects_w3c_traceparent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "otlp")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    reset_tracing_for_tests()
    exporter = InMemorySpanExporter()
    configure_tracing(exporter=exporter)
    captured: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["traceparent"] = request.headers.get("traceparent")
        captured["authorization"] = request.headers.get("authorization")
        return _json_response(200, {"incident_id": "INC-2026-0042", "ok": True})

    try:
        client = EnterpriseApiClient(
            "http://enterprise-api:8080",
            "lab-read-token",
            transport=httpx.MockTransport(handler),
        )
        client.get_incident("INC-2026-0042")
        flush_tracing()
    finally:
        shutdown_tracing()
        reset_tracing_for_tests()

    assert TRACEPARENT_RE.match(captured["traceparent"] or "")
    assert captured["authorization"] == "Bearer lab-read-token"
    clients = [span for span in exporter.get_finished_spans() if span.kind == SpanKind.CLIENT]
    assert len(clients) == 1
    blob = clients[0].to_json()
    assert "lab-read-token" not in blob
    assert "Bearer" not in blob
    assert "INC-2026-0042" not in blob
    assert dict(clients[0].attributes or {})["http.route"] == "/v1/incidents/{incident_id}"


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


@pytest.mark.parametrize(
    "hostile",
    [
        "Bearer lab-read-token",
        "cap_leaked-capability-value",
        "idem-secret-key-material",
        "note-secret-do-not-reflect",
        "x" * 500,
        "safe-looking-but-not-uuid-token-material",
    ],
)
def test_hostile_constructor_correlation_id_is_replaced(hostile: str) -> None:
    seen: dict[str, str | None] = {}
    upstream_correlation = str(__import__("uuid").uuid4())

    def handler(request: httpx.Request) -> httpx.Response:
        seen["sent"] = request.headers.get("X-Correlation-ID")
        return _json_response(
            200,
            {"incident_id": "INC-2026-0042", "ok": True},
            correlation_id=upstream_correlation,
        )

    client = EnterpriseApiClient(
        "http://enterprise-api:8080",
        "lab-read-token",
        correlation_id=hostile,
        transport=httpx.MockTransport(handler),
    )
    assert client.correlation_id != hostile
    assert _UUID_RE.fullmatch(client.correlation_id)
    constructor_replacement = client.correlation_id
    client.get_incident("INC-2026-0042")
    assert seen["sent"] == constructor_replacement
    assert seen["sent"] != hostile
    assert client.correlation_id == upstream_correlation


@pytest.mark.parametrize(
    "hostile",
    [
        "Bearer lab-read-token",
        "cap_leaked-capability-value",
        "idem-secret-key-material",
        "note-secret-do-not-reflect",
        "x" * 500,
        "safe-looking-but-not-uuid-token-material",
    ],
)
def test_hostile_response_correlation_id_is_not_propagated(hostile: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            200,
            {"incident_id": "INC-2026-0042", "ok": True},
            correlation_id=hostile,
        )

    client = EnterpriseApiClient(
        "http://enterprise-api:8080",
        "lab-read-token",
        transport=httpx.MockTransport(handler),
    )
    original = client.correlation_id
    payload, call = client.get_incident("INC-2026-0042")
    assert payload["ok"] is True
    assert hostile not in original
    assert call.correlation_id != hostile
    assert client.correlation_id != hostile
    assert _UUID_RE.fullmatch(call.correlation_id)
    assert _UUID_RE.fullmatch(client.correlation_id)
    assert hostile not in json.dumps({"call": call.correlation_id, "client": client.correlation_id})
