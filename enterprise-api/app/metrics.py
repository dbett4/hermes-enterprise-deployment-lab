from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest
from starlette.requests import Request

METRICS_REGISTRY = CollectorRegistry()

HTTP_REQUESTS = Counter(
    "enterprise_api_http_requests_total",
    "HTTP requests handled by the enterprise API.",
    ("method", "route", "status_class"),
    registry=METRICS_REGISTRY,
)
HTTP_REQUEST_DURATION = Histogram(
    "enterprise_api_http_request_duration_seconds",
    "Enterprise API request latency in seconds.",
    ("method", "route"),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    registry=METRICS_REGISTRY,
)
ACTION_OUTCOMES = Counter(
    "enterprise_api_action_outcomes_total",
    "Incident action outcomes at the enterprise API commit boundary.",
    ("outcome",),
    registry=METRICS_REGISTRY,
)


def route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) and path else "__unmatched__"


def observe_request(request: Request, status_code: int, elapsed_seconds: float) -> None:
    route = route_template(request)
    if route == "/metrics":
        return

    method = request.method
    status_class = f"{status_code // 100}xx"
    HTTP_REQUESTS.labels(method=method, route=route, status_class=status_class).inc()
    HTTP_REQUEST_DURATION.labels(method=method, route=route).observe(elapsed_seconds)


def observe_action_outcome(outcome: str) -> None:
    ACTION_OUTCOMES.labels(outcome=outcome).inc()


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(METRICS_REGISTRY), CONTENT_TYPE_LATEST
