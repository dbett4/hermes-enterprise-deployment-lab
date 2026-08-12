"""Opt-in OpenTelemetry tracing for the workflow runner.

Tracing stays off unless OTEL_TRACES_EXPORTER=otlp and a loopback OTLP/HTTP
endpoint are both set. SimpleSpanProcessor is lab-only: it exports each span
synchronously so proof shutdown is deterministic. A production collector would
use BatchSpanProcessor instead.
"""

from __future__ import annotations

import ipaddress
import os
import threading
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

from opentelemetry import trace
from opentelemetry.propagate import set_global_textmap
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter
from opentelemetry.trace import NoOpTracerProvider, Span, Tracer
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

SERVICE_NAME = "workflow-runner"

ALLOWED_ATTRIBUTE_KEYS = frozenset(
    {
        "http.method",
        "http.route",
        "http.status_class",
        "elapsed_ms",
        "correlation_id",
        "reason",
    }
)
ALLOWED_REASON_VALUES = frozenset(
    {
        "unknown_action_id",
        "unknown_approval_capability",
        "approval_capability_bound_to_different_incident",
        "approval_capability_bound_to_different_action",
        "approval_expired",
        "approval_already_applied",
        "approval_not_granted",
        "approver_identity_required",
    }
)
ALLOWED_EVENTS = frozenset(
    {
        "approval.requested",
        "approval.accepted",
        "mutation.dispatched",
        "mutation.failed_resumable",
        "mutation.applied",
        "mutation.replayed",
        "approval.rejected",
    }
)
_FORBIDDEN_KEY_FRAGMENTS = (
    "token",
    "auth",
    "bearer",
    "cookie",
    "capability",
    "idempotency",
    "note",
    "body",
    "password",
    "secret",
    "incident",
    "action_id",
    "approval_id",
    "record",
    "header",
)
_FORBIDDEN_VALUE_MARKERS = (
    "Bearer ",
    "bearer ",
    "cap_",
    "idem-",
    "apr_",
    "lab-read-token",
    "lab-write-token",
)

_LOCK = threading.Lock()
_provider: TracerProvider | None = None
_configured = False


def _endpoint_from_env() -> str | None:
    traces = os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "").strip()
    base = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if traces:
        return traces
    if base:
        return base.rstrip("/") + "/v1/traces"
    return None


def _is_loopback_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname
    if parsed.scheme not in {"http", "https"} or not host:
        return False
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def tracing_enabled() -> bool:
    if os.environ.get("OTEL_TRACES_EXPORTER", "").strip().lower() != "otlp":
        return False
    endpoint = _endpoint_from_env()
    return bool(endpoint) and _is_loopback_url(endpoint)


def sanitize_attributes(attributes: Mapping[str, Any] | None) -> dict[str, Any]:
    if not attributes:
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in attributes.items():
        if key not in ALLOWED_ATTRIBUTE_KEYS:
            continue
        lowered = key.lower()
        if any(fragment in lowered for fragment in _FORBIDDEN_KEY_FRAGMENTS):
            continue
        if key == "reason" and value not in ALLOWED_REASON_VALUES:
            continue
        if not _safe_value(value):
            continue
        cleaned[key] = value
    return cleaned


def _safe_value(value: Any) -> bool:
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        if len(value) > 128:
            return False
        return not any(marker in value for marker in _FORBIDDEN_VALUE_MARKERS)
    return False


def set_sanitized_attributes(span: Span, attributes: Mapping[str, Any] | None) -> None:
    if not span.is_recording():
        return
    for key, value in sanitize_attributes(attributes).items():
        span.set_attribute(key, value)


def add_bounded_event(span: Span, name: str, attributes: Mapping[str, Any] | None = None) -> None:
    if not span.is_recording() or name not in ALLOWED_EVENTS:
        return
    span.add_event(name, attributes=sanitize_attributes(attributes))


def configure_tracing(*, exporter: SpanExporter | None = None) -> TracerProvider | None:
    """Install a provider when enabled, or when a test supplies an exporter."""
    global _provider, _configured
    with _LOCK:
        # Sticky only while still disabled, or while a provider is already live.
        # A prior disabled configure must not block a later env opt-in.
        if exporter is None and _configured and (_provider is not None or not tracing_enabled()):
            return _provider
        if exporter is None and not tracing_enabled():
            _configured = True
            _provider = None
            return None
        if exporter is None:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            endpoint = _endpoint_from_env()
            if endpoint is None:
                _configured = True
                _provider = None
                return None
            exporter = OTLPSpanExporter(endpoint=endpoint)

        if _provider is not None:
            _provider.shutdown()

        provider = TracerProvider(resource=Resource(attributes={"service.name": SERVICE_NAME}))
        # Lab-only: SimpleSpanProcessor exports on span end so proofs can flush
        # deterministically. Do not copy this into a production collector path.
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        _provider = provider
        _configured = True
        set_global_textmap(TraceContextTextMapPropagator())
        _install_global_provider(provider)
        return provider


def ensure_tracing() -> TracerProvider | None:
    return configure_tracing()


def get_tracer() -> Tracer:
    if _provider is None:
        return trace.get_tracer(SERVICE_NAME, tracer_provider=NoOpTracerProvider())
    return trace.get_tracer(SERVICE_NAME, tracer_provider=_provider)


def flush_tracing() -> None:
    if _provider is not None:
        _provider.force_flush()


def shutdown_tracing() -> None:
    global _provider, _configured
    with _LOCK:
        if _provider is not None:
            _provider.force_flush()
            _provider.shutdown()
        _provider = None
        _configured = False


def reset_tracing_for_tests() -> None:
    """Drop process-wide provider state so tests can reconfigure."""
    shutdown_tracing()
    _reset_global_provider()


def _install_global_provider(provider: TracerProvider) -> None:
    _reset_global_provider()
    trace.set_tracer_provider(provider)


def _reset_global_provider() -> None:
    current = trace.get_tracer_provider()
    shutdown = getattr(current, "shutdown", None)
    if callable(shutdown) and not isinstance(current, NoOpTracerProvider):
        try:
            shutdown()
        except Exception:
            pass
    once = getattr(trace, "_TRACER_PROVIDER_SET_ONCE", None)
    if once is not None and hasattr(once, "_done"):
        once._done = False
    if hasattr(trace, "_TRACER_PROVIDER"):
        trace._TRACER_PROVIDER = None
