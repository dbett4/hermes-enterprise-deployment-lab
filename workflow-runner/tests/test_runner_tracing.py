from __future__ import annotations

import json

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from workflow_runner.tracing import (
    SERVICE_NAME,
    configure_tracing,
    ensure_tracing,
    flush_tracing,
    get_tracer,
    reset_tracing_for_tests,
    sanitize_attributes,
    shutdown_tracing,
)


@pytest.fixture(autouse=True)
def _reset_tracing() -> None:
    reset_tracing_for_tests()
    yield
    shutdown_tracing()
    reset_tracing_for_tests()


def test_tracing_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_TRACES_EXPORTER", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    assert configure_tracing() is None
    span = get_tracer().start_span("noop")
    assert span.is_recording() is False
    span.end()


def test_missing_otlp_exporter_flag_keeps_tracing_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    monkeypatch.delenv("OTEL_TRACES_EXPORTER", raising=False)
    assert configure_tracing() is None


def test_service_name_allowlist_and_flush(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "otlp")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    exporter = InMemorySpanExporter()
    provider = configure_tracing(exporter=exporter)
    assert provider is not None
    with get_tracer().start_as_current_span("runner-flush"):
        pass
    flush_tracing()
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].resource.attributes["service.name"] == SERVICE_NAME
    assert SERVICE_NAME == "workflow-runner"

    cleaned = sanitize_attributes(
        {
            "http.method": "POST",
            "correlation_id": "c1",
            "note": "secret-note",
            "idempotency_key": "idem-abc",
            "approval_capability": "cap_abc",
            "incident_id": "INC-2026-0042",
            "body": "{}",
        }
    )
    assert cleaned == {"http.method": "POST", "correlation_id": "c1"}
    assert "secret-note" not in json.dumps(cleaned)


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
