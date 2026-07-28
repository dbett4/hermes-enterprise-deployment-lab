from __future__ import annotations

import json
from typing import Any

import httpx

from workflow_runner.client import EnterpriseApiClient
from workflow_runner.errors import WorkflowErrorCode
from workflow_runner.planner import run_incident_intake


def _json_response(
    status_code: int,
    payload: dict[str, Any] | str,
    correlation_id: str = "upstream-corr-1",
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

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/runbook"):
            return _json_response(200, runbook)
        return _json_response(200, incident)

    client = EnterpriseApiClient(
        "http://enterprise-api:8080",
        "lab-read-token",
        transport=httpx.MockTransport(handler),
    )
    receipt = run_incident_intake(client, "INC-2026-0042")

    assert receipt.outcome == "success"
    assert receipt.approval_required is True
    assert receipt.correlation_id == "upstream-corr-1"
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
