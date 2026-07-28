import time

import pytest
from fastapi.testclient import TestClient

from app.config import settings


def auth_headers(token: str = "lab-read-token", correlation_id: str = "test-corr-123") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Correlation-ID": correlation_id,
    }


def test_healthz(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz(client: TestClient) -> None:
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_get_incident_requires_auth(client: TestClient) -> None:
    response = client.get("/v1/incidents/INC-2026-0042")
    assert response.status_code == 401


def test_get_incident_rejects_bad_token(client: TestClient) -> None:
    response = client.get(
        "/v1/incidents/INC-2026-0042",
        headers=auth_headers(token="wrong-token"),
    )
    assert response.status_code == 403


def test_get_incident_success(client: TestClient) -> None:
    response = client.get(
        "/v1/incidents/INC-2026-0042",
        headers=auth_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["incident_id"] == "INC-2026-0042"
    assert body["severity"] == "high"
    assert response.headers["X-Correlation-ID"] == "test-corr-123"


def test_get_incident_not_found(client: TestClient) -> None:
    response = client.get(
        "/v1/incidents/INC-MISSING",
        headers=auth_headers(),
    )
    assert response.status_code == 404


def test_get_runbook_success(client: TestClient) -> None:
    response = client.get(
        "/v1/incidents/INC-2026-0042/runbook",
        headers=auth_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["runbook_id"] == "RB-PAY-GATEWAY-01"
    assert len(body["steps"]) >= 1


def test_correlation_id_generated_when_missing(client: TestClient) -> None:
    response = client.get(
        "/v1/incidents/INC-2026-0042",
        headers={"Authorization": "Bearer lab-read-token"},
    )
    assert response.status_code == 200
    assert response.headers.get("X-Correlation-ID")


def test_inject_error_via_query(client: TestClient) -> None:
    response = client.get(
        "/v1/incidents/INC-2026-0042?inject=error",
        headers=auth_headers(),
    )
    assert response.status_code == 500


def test_inject_error_via_header(client: TestClient) -> None:
    headers = auth_headers()
    headers["X-Inject-Failure"] = "error"
    response = client.get("/v1/incidents/INC-2026-0042", headers=headers)
    assert response.status_code == 500


def test_inject_timeout_via_query(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "inject_timeout_seconds", 0.2)
    started = time.perf_counter()
    response = client.get(
        "/v1/incidents/INC-2026-0042?inject=timeout",
        headers=auth_headers(),
    )
    elapsed = time.perf_counter() - started
    assert response.status_code == 200
    assert elapsed >= 0.2
