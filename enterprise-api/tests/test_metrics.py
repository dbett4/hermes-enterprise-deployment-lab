from __future__ import annotations

from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from app.store import ACTION_STORE

READ = {"Authorization": "Bearer lab-read-token"}
WRITE = {"Authorization": "Bearer lab-write-token"}
INCIDENT = "INC-2026-0042"
ROUTE = "/v1/incidents/{incident_id}"
ACTION_PATH = f"/v1/incidents/{INCIDENT}/actions"


def _sample_value(
    text: str,
    metric: str,
    labels: dict[str, str],
    *,
    missing: float | None = None,
) -> float:
    for family in text_string_to_metric_families(text):
        for sample in family.samples:
            if sample.name == metric and sample.labels == labels:
                return float(sample.value)
    if missing is not None:
        return missing
    raise AssertionError(f"missing sample {metric}{labels}")


def test_metrics_scrape_uses_route_templates_and_records_success(client: TestClient) -> None:
    before = client.get("/metrics")
    assert before.status_code == 200

    response = client.get(f"/v1/incidents/{INCIDENT}", headers=READ)
    assert response.status_code == 200

    after = client.get("/metrics")
    assert after.status_code == 200
    assert after.headers["content-type"] == "text/plain; version=1.0.0; charset=utf-8"

    labels = {"method": "GET", "route": ROUTE, "status_class": "2xx"}
    before_count = _sample_value(
        before.text,
        "enterprise_api_http_requests_total",
        labels,
        missing=0,
    )
    after_count = _sample_value(after.text, "enterprise_api_http_requests_total", labels)
    assert after_count == before_count + 1

    histogram_labels = {"method": "GET", "route": ROUTE, "le": "0.5"}
    assert _sample_value(
        after.text,
        "enterprise_api_http_request_duration_seconds_bucket",
        histogram_labels,
    ) >= 1
    assert INCIDENT not in after.text


def test_action_metrics_distinguish_created_replayed_and_postcommit_error(
    client: TestClient,
) -> None:
    ACTION_STORE.reset()
    before = client.get("/metrics").text
    body = {"action_id": "RB-PAY-GATEWAY-01-S2", "note": "metrics test"}

    try:
        created = client.post(
            ACTION_PATH,
            json=body,
            headers={**WRITE, "Idempotency-Key": "metrics-created"},
        )
        replayed = client.post(
            ACTION_PATH,
            json=body,
            headers={**WRITE, "Idempotency-Key": "metrics-created"},
        )
        postcommit_error = client.post(
            f"{ACTION_PATH}?inject=error_after_commit",
            json=body,
            headers={**WRITE, "Idempotency-Key": "metrics-postcommit"},
        )

        assert created.status_code == 201
        assert replayed.status_code == 200
        assert postcommit_error.status_code == 500

        after = client.get("/metrics").text
        for outcome in ("created", "replayed", "postcommit_error"):
            labels = {"outcome": outcome}
            assert _sample_value(
                after,
                "enterprise_api_action_outcomes_total",
                labels,
            ) == _sample_value(
                before,
                "enterprise_api_action_outcomes_total",
                labels,
                missing=0,
            ) + 1
    finally:
        ACTION_STORE.reset()
