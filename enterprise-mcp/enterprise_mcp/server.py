from __future__ import annotations

import logging
import sys
import uuid
from typing import Any

import httpx
from fastmcp import FastMCP

from enterprise_mcp.config import load_settings
from enterprise_mcp.context import fetch_incident_context
from workflow_runner.client import EnterpriseApiClient
from workflow_runner.planner import run_incident_intake

# MCP stdio framing uses stdout; keep diagnostics on stderr only.
logging.basicConfig(stream=sys.stderr, level=logging.WARNING, format="%(message)s")

mcp = FastMCP("enterprise-ops")


def _build_client(correlation_id: str | None = None) -> EnterpriseApiClient:
    settings = load_settings()
    return EnterpriseApiClient(
        base_url=settings.api_url,
        token=settings.api_token,
        timeout_seconds=settings.timeout_seconds,
        correlation_id=correlation_id,
    )


@mcp.tool
def check_enterprise_api() -> dict[str, Any]:
    """Check enterprise API health and readiness without exposing credentials.

    Returns the configured base URL, health/readiness status, and correlation ID.
    """
    settings = load_settings()
    correlation_id = str(uuid.uuid4())
    headers = {"X-Correlation-ID": correlation_id, "Accept": "application/json"}

    results: dict[str, Any] = {
        "base_url": settings.api_url,
        "correlation_id": correlation_id,
        "health": {"status": "unknown"},
        "readiness": {"status": "unknown"},
        "outcome": "success",
    }

    try:
        with httpx.Client(timeout=settings.timeout_seconds) as client:
            health = client.get(f"{settings.api_url}/healthz", headers=headers)
            results["health"] = {
                "status_code": health.status_code,
                "body": health.json() if health.status_code == 200 else None,
            }
            response_corr = health.headers.get("X-Correlation-ID")
            if response_corr:
                results["correlation_id"] = response_corr

            ready = client.get(f"{settings.api_url}/readyz", headers=headers)
            results["readiness"] = {
                "status_code": ready.status_code,
                "body": ready.json() if ready.status_code == 200 else None,
            }
            response_corr = ready.headers.get("X-Correlation-ID")
            if response_corr:
                results["correlation_id"] = response_corr

            if health.status_code != 200 or ready.status_code != 200:
                results["outcome"] = "degraded"
    except httpx.TimeoutException:
        results["outcome"] = "error"
        results["error"] = {"code": "timeout", "message": "Health check timed out"}
    except httpx.HTTPError as exc:
        results["outcome"] = "error"
        results["error"] = {"code": "connection_error", "message": str(exc)}

    return results


@mcp.tool
def get_incident_context(incident_id: str) -> dict[str, Any]:
    """Retrieve incident and runbook context with dependency-call evidence.

    Args:
        incident_id: Fixture incident identifier (for example INC-2026-0042).
    """
    client = _build_client()
    return fetch_incident_context(client, incident_id)


@mcp.tool
def propose_incident_plan(incident_id: str) -> dict[str, Any]:
    """Build an approval-gated incident action plan without executing mutations.

    Args:
        incident_id: Fixture incident identifier (for example INC-2026-0042).

    Returns a structured receipt with proposed actions. Consequential runbook steps
    always require human approval; this tool never executes external changes.
    """
    client = _build_client()
    receipt = run_incident_intake(client, incident_id)
    payload = receipt.model_dump()
    if receipt.outcome == "success" and any(
        action.get("approval_required") for action in payload.get("proposed_actions", [])
    ):
        payload["approval_required"] = True
    return payload


def main() -> None:
    mcp.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
