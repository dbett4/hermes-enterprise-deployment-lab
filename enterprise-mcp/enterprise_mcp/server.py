from __future__ import annotations

import logging
import sys
import uuid
from typing import Any

import httpx
from fastmcp import FastMCP

from enterprise_mcp.config import ConfigurationError, enabled_tools_from_env, load_settings
from enterprise_mcp.context import fetch_incident_context
from workflow_runner.audit import AuditLog
from workflow_runner.client import EnterpriseApiClient
from workflow_runner.executor import apply_incident_action
from workflow_runner.planner import run_incident_intake

# MCP stdio framing uses stdout; keep diagnostics on stderr only.
logging.basicConfig(stream=sys.stderr, level=logging.WARNING, format="%(message)s")


def _build_client(write: bool = False, correlation_id: str | None = None) -> EnterpriseApiClient:
    settings = load_settings()
    token = settings.api_write_token if (write and settings.api_write_token) else settings.api_token
    return EnterpriseApiClient(
        base_url=settings.api_url,
        token=token,
        timeout_seconds=settings.timeout_seconds,
        correlation_id=correlation_id,
    )


def _audit_invocation(tool: str, **fields: Any) -> None:
    AuditLog().append("tool_invoked", actor="enterprise-mcp", tool=tool, **fields)


# --------------------------------------------------------------------------
# Tool implementations. Plain functions, so the allowlist decides what gets
# registered; an excluded tool is never handed to FastMCP at all.
# --------------------------------------------------------------------------


def check_enterprise_api() -> dict[str, Any]:
    """Check enterprise API health and readiness without exposing credentials.

    Returns the configured base URL, health/readiness status, and correlation ID.
    """
    settings = load_settings()
    correlation_id = str(uuid.uuid4())
    headers = {"X-Correlation-ID": correlation_id, "Accept": "application/json"}
    _audit_invocation("check_enterprise_api", correlation_id=correlation_id)

    results: dict[str, Any] = {
        "base_url": settings.api_url,
        "correlation_id": correlation_id,
        "health": {"status": "unknown"},
        "readiness": {"status": "unknown"},
        "mutation_capability": "write_token_present" if settings.can_mutate else "read_only",
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


def get_incident_context(incident_id: str) -> dict[str, Any]:
    """Retrieve incident and runbook context with dependency-call evidence.

    Args:
        incident_id: Fixture incident identifier (for example INC-2026-0042).
    """
    client = _build_client()
    _audit_invocation(
        "get_incident_context",
        correlation_id=client.correlation_id,
        incident_id=incident_id,
    )
    return fetch_incident_context(client, incident_id)


def propose_incident_plan(incident_id: str) -> dict[str, Any]:
    """Build an approval-gated incident action plan without executing mutations.

    Args:
        incident_id: Fixture incident identifier (for example INC-2026-0042).

    Returns a structured receipt with proposed actions. Consequential runbook steps
    always require human approval; this tool never executes external changes.
    """
    client = _build_client()
    _audit_invocation(
        "propose_incident_plan",
        correlation_id=client.correlation_id,
        incident_id=incident_id,
    )
    receipt = run_incident_intake(client, incident_id)
    payload = receipt.model_dump()
    if receipt.outcome == "success" and any(
        action.get("approval_required") for action in payload.get("proposed_actions", [])
    ):
        payload["approval_required"] = True
    return payload


def apply_incident_plan(
    incident_id: str,
    action_id: str,
    approval_token: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Execute one approved runbook action against the enterprise API.

    This is the only mutating tool. Calling it WITHOUT approval_token changes
    nothing: it returns status "pending_approval" plus a single-purpose approval
    token that a human must supply to proceed. Calling it again with that token
    performs the write exactly once, because the approval carries an idempotency
    key that survives a failure and resume.

    Args:
        incident_id: Fixture incident identifier (for example INC-2026-0042).
        action_id: Runbook step identifier (for example RB-PAY-GATEWAY-01-S2).
        approval_token: Token issued by a prior un-approved call. Omit to request approval.
        note: Optional operator note recorded with the side effect.
    """
    settings = load_settings()
    client = _build_client(write=True)
    _audit_invocation(
        "apply_incident_plan",
        correlation_id=client.correlation_id,
        incident_id=incident_id,
        action_id=action_id,
        approval_token_presented=bool(approval_token),
    )
    result = apply_incident_action(
        client,
        incident_id=incident_id,
        action_id=action_id,
        approval_token=approval_token,
        note=note,
        inject=settings.inject_failure,
    )
    result["credential_scope"] = "write_token" if settings.can_mutate else "read_token_only"
    if not settings.can_mutate:
        result.setdefault(
            "scope_note",
            "No ENTERPRISE_API_WRITE_TOKEN was supplied, so this tool can only present "
            "read-scope credentials and the API refuses the mutation.",
        )
    return result


TOOL_IMPLEMENTATIONS = {
    "check_enterprise_api": check_enterprise_api,
    "get_incident_context": get_incident_context,
    "propose_incident_plan": propose_incident_plan,
    "apply_incident_plan": apply_incident_plan,
}


def build_server(enabled_tools: frozenset[str] | set[str] | None = None) -> FastMCP:
    """Construct the MCP server exposing exactly the allowlisted tools.

    Filtering happens here, before registration. A tool outside the allowlist is
    absent from list_tools and is not callable — it does not exist on the wire.
    """
    enabled = frozenset(enabled_tools) if enabled_tools is not None else enabled_tools_from_env()
    server = FastMCP("enterprise-ops")
    for name in sorted(enabled):
        implementation = TOOL_IMPLEMENTATIONS.get(name)
        if implementation is None:  # pragma: no cover - resolve_enabled_tools rejects these
            raise ConfigurationError(f"No implementation registered for tool {name}")
        server.tool(implementation)
    return server


mcp = build_server()


def main() -> None:
    # Fail closed before serving: a missing credential must be loud, not a silent
    # fallback that produces authoritative-looking output.
    try:
        load_settings()
    except ConfigurationError as exc:
        print(f"enterprise-mcp configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    mcp.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
