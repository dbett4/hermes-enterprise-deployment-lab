from __future__ import annotations

import json
import logging
import time
from typing import Callable

from opentelemetry import context
from opentelemetry.propagate import extract
from opentelemetry.trace import SpanKind, Status, StatusCode
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings
from app.correlation import normalize_correlation_id
from app.metrics import observe_request, route_template
from app.tracing import ensure_tracing, get_tracer, set_sanitized_attributes

logger = logging.getLogger(settings.service_name)


def _traceparent(trace_id: int, span_id: int) -> str:
    return f"00-{format(trace_id, '032x')}-{format(span_id, '016x')}-01"


class CorrelationAndLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        ensure_tracing()
        correlation_id = normalize_correlation_id(request.headers.get("X-Correlation-ID"))
        request.state.correlation_id = correlation_id
        started = time.perf_counter()
        token = context.attach(extract(request.headers))
        try:
            with get_tracer().start_as_current_span(
                request.method,
                kind=SpanKind.SERVER,
                record_exception=False,
                set_status_on_exception=False,
            ) as span:
                set_sanitized_attributes(
                    span,
                    {
                        "http.method": request.method,
                        "correlation_id": correlation_id,
                    },
                )
                try:
                    response = await call_next(request)
                except Exception:
                    elapsed_seconds = time.perf_counter() - started
                    elapsed_ms = round(elapsed_seconds * 1000, 2)
                    observe_request(request, 500, elapsed_seconds)
                    route = route_template(request)
                    span.update_name(f"{request.method} {route}")
                    set_sanitized_attributes(
                        span,
                        {
                            "http.route": route,
                            "http.status_class": "5xx",
                            "elapsed_ms": elapsed_ms,
                        },
                    )
                    span.set_status(Status(StatusCode.ERROR))
                    raise

                elapsed_seconds = time.perf_counter() - started
                elapsed_ms = round(elapsed_seconds * 1000, 2)
                observe_request(request, response.status_code, elapsed_seconds)
                route = route_template(request)
                status_class = f"{response.status_code // 100}xx"
                span.update_name(f"{request.method} {route}")
                set_sanitized_attributes(
                    span,
                    {
                        "http.route": route,
                        "http.status_class": status_class,
                        "elapsed_ms": elapsed_ms,
                    },
                )
                if response.status_code >= 500:
                    span.set_status(Status(StatusCode.ERROR))

                response.headers["X-Correlation-ID"] = correlation_id
                span_context = span.get_span_context()
                log_payload = {
                    "service": settings.service_name,
                    "route": route,
                    "method": request.method,
                    "status": response.status_code,
                    "correlation_id": correlation_id,
                    "elapsed_ms": elapsed_ms,
                }
                if span_context.is_valid:
                    response.headers["traceparent"] = _traceparent(
                        span_context.trace_id, span_context.span_id
                    )
                    log_payload["trace_id"] = format(span_context.trace_id, "032x")
                    log_payload["span_id"] = format(span_context.span_id, "016x")
                logger.info(json.dumps(log_payload))
                return response
        finally:
            context.detach(token)
