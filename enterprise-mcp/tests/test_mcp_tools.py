from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastmcp.client.transports.memory import FastMCPTransport

from enterprise_mcp.server import mcp
from workflow_runner.errors import WorkflowErrorCode


def _json_response(
    status_code: int,
    payload: dict[str, Any] | str,
    correlation_id: str = "mcp-corr-1",
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


def _mock_handler(
    incident: dict[str, Any] | None = None,
    runbook: dict[str, Any] | None = None,
    incident_status: int = 200,
    runbook_status: int = 200,
):
    incident_payload = incident or {
        "incident_id": "INC-2026-0042",
        "severity": "high",
        "affected_service": "payment-gateway",
    }
    runbook_payload = runbook or {
        "runbook_id": "RB-PAY-GATEWAY-01",
        "steps": [{"action": "Scale replicas", "approval_required": True}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/healthz"):
            return _json_response(200, {"status": "ok"})
        if request.url.path.endswith("/readyz"):
            return _json_response(200, {"status": "ready"})
        if request.url.path.endswith("/runbook"):
            if runbook_status >= 400:
                return _json_response(runbook_status, {"detail": "not found"})
            return _json_response(200, runbook_payload)
        if runbook_status >= 400 and "/incidents/" in request.url.path:
            return _json_response(incident_status, {"detail": "not found"})
        if incident_status >= 400:
            return _json_response(incident_status, {"detail": "unauthorized"})
        return _json_response(200, incident_payload)

    return handler


@pytest.fixture
def mcp_transport(monkeypatch: pytest.MonkeyPatch) -> FastMCPTransport:
    handler = _mock_handler()
    mock_transport = httpx.MockTransport(handler)
    monkeypatch.setenv("ENTERPRISE_API_URL", "http://enterprise-api:8080")
    monkeypatch.setenv("ENTERPRISE_API_TOKEN", "lab-read-token")
    monkeypatch.setenv("ENTERPRISE_API_TIMEOUT_SECONDS", "5")

    real_client = httpx.Client

    def factory(**kwargs):
        kwargs["transport"] = mock_transport
        return real_client(**kwargs)

    monkeypatch.setattr(httpx, "Client", factory)
    return FastMCPTransport(mcp)


def _patch_httpx_client(monkeypatch: pytest.MonkeyPatch, mock_transport: httpx.MockTransport) -> None:
    real_client = httpx.Client

    def factory(**kwargs):
        kwargs["transport"] = mock_transport
        return real_client(**kwargs)

    monkeypatch.setattr(httpx, "Client", factory)


@pytest.mark.asyncio
async def test_tool_list_contains_only_three_tools(mcp_transport: FastMCPTransport) -> None:
    from fastmcp import Client

    async with Client(mcp_transport) as client:
        tools = await client.list_tools()
    names = sorted(tool.name for tool in tools)
    assert names == [
        "check_enterprise_api",
        "get_incident_context",
        "propose_incident_plan",
    ]


@pytest.mark.asyncio
async def test_check_enterprise_api_success(mcp_transport: FastMCPTransport) -> None:
    from fastmcp import Client

    async with Client(mcp_transport) as client:
        result = await client.call_tool("check_enterprise_api", {})
    payload = result.data
    assert payload["outcome"] == "success"
    assert payload["base_url"] == "http://enterprise-api:8080"
    assert payload["health"]["status_code"] == 200
    assert payload["readiness"]["status_code"] == 200
    assert "lab-read-token" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_get_incident_context_success(mcp_transport: FastMCPTransport) -> None:
    from fastmcp import Client

    async with Client(mcp_transport) as client:
        result = await client.call_tool(
            "get_incident_context",
            {"incident_id": "INC-2026-0042"},
        )
    payload = result.data
    assert payload["outcome"] == "success"
    assert payload["incident"]["incident_id"] == "INC-2026-0042"
    assert payload["runbook"]["runbook_id"] == "RB-PAY-GATEWAY-01"
    assert len(payload["dependency_calls"]) == 2
    assert payload["correlation_id"] == "mcp-corr-1"


@pytest.mark.asyncio
async def test_propose_incident_plan_success_requires_approval(
    mcp_transport: FastMCPTransport,
) -> None:
    from fastmcp import Client

    async with Client(mcp_transport) as client:
        result = await client.call_tool(
            "propose_incident_plan",
            {"incident_id": "INC-2026-0042"},
        )
    payload = result.data
    assert payload["outcome"] == "success"
    assert payload["approval_required"] is True
    assert payload["proposed_actions"]
    assert "lab-read-token" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_auth_failure_through_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastmcp import Client

    handler = _mock_handler(incident_status=401)
    transport = httpx.MockTransport(handler)
    monkeypatch.setenv("ENTERPRISE_API_URL", "http://enterprise-api:8080")
    monkeypatch.setenv("ENTERPRISE_API_TOKEN", "bad-token")
    _patch_httpx_client(monkeypatch, transport)

    async with Client(FastMCPTransport(mcp)) as client:
        result = await client.call_tool(
            "get_incident_context",
            {"incident_id": "INC-2026-0042"},
        )
    payload = result.data
    assert payload["outcome"] == "error"
    assert payload["error"]["code"] == WorkflowErrorCode.AUTH_FAILURE.value
    assert "bad-token" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_not_found_through_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastmcp import Client

    handler = _mock_handler(incident_status=404, runbook_status=404)
    transport = httpx.MockTransport(handler)
    monkeypatch.setenv("ENTERPRISE_API_URL", "http://enterprise-api:8080")
    monkeypatch.setenv("ENTERPRISE_API_TOKEN", "lab-read-token")
    _patch_httpx_client(monkeypatch, transport)

    async with Client(FastMCPTransport(mcp)) as client:
        result = await client.call_tool(
            "get_incident_context",
            {"incident_id": "INC-MISSING"},
        )
    payload = result.data
    assert payload["outcome"] == "error"
    assert payload["error"]["code"] == WorkflowErrorCode.NOT_FOUND.value


@pytest.mark.asyncio
async def test_upstream_5xx_through_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastmcp import Client

    handler = _mock_handler(incident_status=500)
    transport = httpx.MockTransport(handler)
    monkeypatch.setenv("ENTERPRISE_API_URL", "http://enterprise-api:8080")
    monkeypatch.setenv("ENTERPRISE_API_TOKEN", "lab-read-token")
    _patch_httpx_client(monkeypatch, transport)

    async with Client(FastMCPTransport(mcp)) as client:
        result = await client.call_tool(
            "propose_incident_plan",
            {"incident_id": "INC-2026-0042"},
        )
    payload = result.data
    assert payload["outcome"] == "error"
    assert payload["error"]["code"] == WorkflowErrorCode.UPSTREAM_5XX.value


@pytest.mark.asyncio
async def test_timeout_through_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastmcp import Client

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=httpx.Request("GET", "http://test"))

    transport = httpx.MockTransport(handler)
    monkeypatch.setenv("ENTERPRISE_API_URL", "http://enterprise-api:8080")
    monkeypatch.setenv("ENTERPRISE_API_TOKEN", "lab-read-token")
    monkeypatch.setenv("ENTERPRISE_API_TIMEOUT_SECONDS", "0.01")
    _patch_httpx_client(monkeypatch, transport)

    async with Client(FastMCPTransport(mcp)) as client:
        result = await client.call_tool(
            "get_incident_context",
            {"incident_id": "INC-2026-0042"},
        )
    payload = result.data
    assert payload["outcome"] == "error"
    assert payload["error"]["code"] == WorkflowErrorCode.TIMEOUT.value


@pytest.mark.asyncio
async def test_malformed_response_through_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastmcp import Client

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"not-json",
            headers={"Content-Type": "text/plain"},
            request=httpx.Request("GET", "http://test"),
        )

    transport = httpx.MockTransport(handler)
    monkeypatch.setenv("ENTERPRISE_API_URL", "http://enterprise-api:8080")
    monkeypatch.setenv("ENTERPRISE_API_TOKEN", "lab-read-token")
    _patch_httpx_client(monkeypatch, transport)

    async with Client(FastMCPTransport(mcp)) as client:
        result = await client.call_tool(
            "get_incident_context",
            {"incident_id": "INC-2026-0042"},
        )
    payload = result.data
    assert payload["outcome"] == "error"
    assert payload["error"]["code"] == WorkflowErrorCode.MALFORMED_RESPONSE.value
    assert "lab-read-token" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_no_token_leakage_in_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastmcp import Client

    secret = "super-secret-lab-token-xyz"
    handler = _mock_handler(incident_status=401)
    transport = httpx.MockTransport(handler)
    monkeypatch.setenv("ENTERPRISE_API_URL", "http://enterprise-api:8080")
    monkeypatch.setenv("ENTERPRISE_API_TOKEN", secret)
    _patch_httpx_client(monkeypatch, transport)

    async with Client(FastMCPTransport(mcp)) as client:
        result = await client.call_tool(
            "propose_incident_plan",
            {"incident_id": "INC-2026-0042"},
        )
    blob = json.dumps(result.data)
    assert secret not in blob
