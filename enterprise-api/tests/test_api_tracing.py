from __future__ import annotations

import json
import logging
import re

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from app.tracing import (
    SERVICE_NAME,
    configure_tracing,
    ensure_tracing,
    flush_tracing,
    get_tracer,
    reset_tracing_for_tests,
    sanitize_attributes,
    shutdown_tracing,
)

READ = {"Authorization": "Bearer lab-read-token"}
INCIDENT = "INC-2026-0042"
TRACEPARENT_RE = re.compile(r"^00-[0-9a-f]{32}-[0-9a-f]{16}-0[0-9a-f]$")


@pytest.fixture(autouse=True)
def _reset_tracing() -> None:
    reset_tracing_for_tests()
    yield
    shutdown_tracing()
    reset_tracing_for_tests()


def _enable(monkeypatch: pytest.MonkeyPatch) -> InMemorySpanExporter:
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "otlp")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    exporter = InMemorySpanExporter()
    assert configure_tracing(exporter=exporter) is not None
    return exporter


def test_tracing_is_disabled_without_exporter_and_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OTEL_TRACES_EXPORTER", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)

    assert configure_tracing() is None
    span = get_tracer().start_span("disabled")
    assert span.is_recording() is False
    span.end()


def test_non_loopback_endpoint_keeps_tracing_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "otlp")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://example.invalid:4318")
    assert configure_tracing() is None


def test_service_name_and_flush(monkeypatch: pytest.MonkeyPatch) -> None:
    exporter = _enable(monkeypatch)
    with get_tracer().start_as_current_span("flush-me"):
        pass
    flush_tracing()
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].resource.attributes["service.name"] == SERVICE_NAME
    assert SERVICE_NAME == "enterprise-api"


def test_sanitize_allowlist_drops_secrets_and_dynamic_ids() -> None:
    cleaned = sanitize_attributes(
        {
            "http.method": "POST",
            "http.route": "/v1/incidents/{incident_id}/actions",
            "http.status_class": "5xx",
            "elapsed_ms": 12.5,
            "correlation_id": "corr-safe",
            "authorization": "Bearer lab-read-token",
            "idempotency_key": "idem-secret",
            "approval_capability": "cap_secret",
            "note": "do not trace this note",
            "body": {"action_id": "RB-PAY-GATEWAY-01-S2"},
            "incident_id": INCIDENT,
            "action_id": "RB-PAY-GATEWAY-01-S2",
            "approval_id": "apr_secret",
        }
    )
    assert cleaned == {
        "http.method": "POST",
        "http.route": "/v1/incidents/{incident_id}/actions",
        "http.status_class": "5xx",
        "elapsed_ms": 12.5,
        "correlation_id": "corr-safe",
    }
    serialized = json.dumps(cleaned)
    assert "Bearer" not in serialized
    assert "lab-read-token" not in serialized
    assert "idem-secret" not in serialized
    assert "cap_secret" not in serialized
    assert "do not trace this note" not in serialized
    assert INCIDENT not in serialized
    assert "RB-PAY-GATEWAY-01-S2" not in serialized
    assert "apr_secret" not in serialized


def test_server_span_propagates_traceparent_and_sanitizes_attributes(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    exporter = _enable(monkeypatch)
    incoming = "00-" + ("a" * 32) + "-" + ("b" * 16) + "-01"
    correlation_id = "22222222-2222-4222-8222-222222222222"
    caplog.set_level(logging.INFO)

    response = client.get(
        f"/v1/incidents/{INCIDENT}",
        headers={**READ, "X-Correlation-ID": correlation_id, "traceparent": incoming},
    )

    assert response.status_code == 200
    assert response.headers["X-Correlation-ID"] == correlation_id
    outgoing = response.headers["traceparent"]
    assert TRACEPARENT_RE.match(outgoing)
    assert outgoing.split("-")[1] == "a" * 32
    assert outgoing.split("-")[2] != "b" * 16

    flush_tracing()
    servers = [span for span in exporter.get_finished_spans() if span.kind == SpanKind.SERVER]
    assert len(servers) == 1
    span = servers[0]
    assert format(span.context.trace_id, "032x") == "a" * 32
    attrs = dict(span.attributes or {})
    assert attrs["http.method"] == "GET"
    assert attrs["http.route"] == "/v1/incidents/{incident_id}"
    assert attrs["http.status_class"] == "2xx"
    assert attrs["correlation_id"] == correlation_id
    assert "elapsed_ms" in attrs
    blob = span.to_json()
    assert INCIDENT not in blob
    assert "lab-read-token" not in blob
    assert "Authorization" not in blob
    assert "Bearer" not in blob

    log_lines = [json.loads(rec.message) for rec in caplog.records if rec.message.startswith("{")]
    matching = [row for row in log_lines if row.get("correlation_id") == correlation_id]
    assert matching
    assert matching[0]["route"] == "/v1/incidents/{incident_id}"
    assert matching[0]["trace_id"] == "a" * 32
    assert matching[0]["span_id"] == format(span.context.span_id, "016x")
    assert INCIDENT not in json.dumps(matching[0])
    assert "Authorization" not in json.dumps(matching[0])


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
def test_hostile_correlation_id_is_replaced_not_reflected(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    hostile: str,
) -> None:
    exporter = _enable(monkeypatch)
    caplog.set_level(logging.INFO)
    response = client.get(
        f"/v1/incidents/{INCIDENT}",
        headers={**READ, "X-Correlation-ID": hostile},
    )
    assert response.status_code == 200
    returned = response.headers["X-Correlation-ID"]
    assert returned != hostile
    assert re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        returned,
    )

    flush_tracing()
    servers = [span for span in exporter.get_finished_spans() if span.kind == SpanKind.SERVER]
    assert servers
    attrs = dict(servers[0].attributes or {})
    assert attrs["correlation_id"] == returned
    assert hostile not in json.dumps(attrs)
    assert hostile not in servers[0].to_json()

    log_blob = "\n".join(rec.message for rec in caplog.records)
    assert hostile not in log_blob
    assert any(
        json.loads(rec.message).get("correlation_id") == returned
        for rec in caplog.records
        if rec.message.startswith("{")
    )


def test_http_exception_500_response_sets_error_status_without_body_leak(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTPException becomes an HTTP 500 response; it is not an unhandled exception."""
    exporter = _enable(monkeypatch)
    response = client.get(
        f"/v1/incidents/{INCIDENT}?inject=error",
        headers=READ,
    )
    assert response.status_code == 500
    flush_tracing()
    servers = [span for span in exporter.get_finished_spans() if span.kind == SpanKind.SERVER]
    assert servers
    span = servers[-1]
    assert span.status.status_code == StatusCode.ERROR
    blob = span.to_json()
    assert "Injected upstream failure" not in blob
    assert "lab-read-token" not in blob
    assert dict(span.attributes or {})["http.status_class"] == "5xx"
    assert not any(event.name == "exception" for event in span.events)


def test_unhandled_exception_does_not_export_secret_markers(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    exporter = _enable(monkeypatch)
    markers = (
        "Bearer ",
        "lab-read-token",
        "cap_fake",
        "idem-secret",
        "note-secret",
    )
    boom = "unhandled " + " ".join(markers)

    async def explode(_mode: str | None) -> None:
        raise RuntimeError(boom)

    monkeypatch.setattr("app.main._apply_failure_injection", explode)
    caplog.set_level(logging.INFO)

    with pytest.raises(RuntimeError, match="unhandled"):
        client.get(
            f"/v1/incidents/{INCIDENT}?inject=error",
            headers=READ,
        )

    flush_tracing()
    servers = [span for span in exporter.get_finished_spans() if span.kind == SpanKind.SERVER]
    assert servers
    span = servers[-1]
    assert span.status.status_code == StatusCode.ERROR
    assert not span.status.description

    blob = span.to_json()
    event_blob = json.dumps(
        [
            {"name": event.name, "attributes": dict(event.attributes or {})}
            for event in span.events
        ]
    )
    log_blob = "\n".join(rec.message for rec in caplog.records)
    for marker in markers:
        assert marker not in blob
        assert marker not in event_blob
        assert marker not in log_blob
    assert not any(event.name == "exception" for event in span.events)


def test_disabled_then_enabled_configures_without_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OTEL_TRACES_EXPORTER", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)

    assert configure_tracing() is None

    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "otlp")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    provider = ensure_tracing()
    assert provider is not None
    assert provider.resource.attributes["service.name"] == SERVICE_NAME
