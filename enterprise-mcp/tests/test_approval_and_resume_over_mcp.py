"""End-to-end proof over real MCP stdio against a real HTTP API.

These are the tests the reviewer gaps 1-5 hinge on. Nothing is mocked: a server
subprocess is spawned per session, and the enterprise API is a live uvicorn
process. The only thing absent is a model choosing the tools.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from workflow_runner.audit import AuditLog

REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHONPATH = f"{REPO_ROOT / 'enterprise-mcp'}:{REPO_ROOT / 'workflow-runner'}"
INCIDENT = "INC-2026-0042"
ACTION = "RB-PAY-GATEWAY-01-S2"
READ_TOKEN = "lab-read-token"
WRITE_TOKEN = "lab-write-token"


def _env(
    tmp_path: Path,
    api_url: str,
    *,
    write_token: str | None = WRITE_TOKEN,
    inject: str | None = None,
    run_id: str = "mcp-arc-test",
) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "PYTHONPATH": PYTHONPATH,
        "ENTERPRISE_API_URL": api_url,
        "ENTERPRISE_API_TOKEN": READ_TOKEN,
        "ENTERPRISE_MCP_ENABLED_TOOLS": "all",
        "AUDIT_LOG_PATH": str(tmp_path / "audit.jsonl"),
        "APPROVAL_STORE_PATH": str(tmp_path / "approvals.json"),
        "AUDIT_RUN_ID": run_id,
    }
    if write_token:
        env["ENTERPRISE_API_WRITE_TOKEN"] = write_token
    if inject:
        env["ENTERPRISE_INJECT_FAILURE"] = inject
    return env


def _client(env: dict[str, str]) -> Client:
    return Client(
        StdioTransport(
            command=sys.executable,
            args=["-m", "enterprise_mcp.server"],
            env=env,
            cwd=str(REPO_ROOT),
        )
    )


def _store_count(api_url: str) -> int:
    response = httpx.get(
        f"{api_url}/v1/incidents/{INCIDENT}/actions",
        headers={"Authorization": f"Bearer {READ_TOKEN}"},
        timeout=10,
    )
    response.raise_for_status()
    return int(response.json()["count"])


@pytest.mark.asyncio
async def test_unapproved_call_makes_no_side_effect(clean_action_store: str, tmp_path: Path) -> None:
    api_url = clean_action_store
    async with _client(_env(tmp_path, api_url)) as client:
        result = await client.call_tool(
            "apply_incident_plan", {"incident_id": INCIDENT, "action_id": ACTION}
        )
    payload = result.data
    assert payload["status"] == "pending_approval"
    assert payload["side_effect"] is None
    assert payload["approval_token"].startswith("apv_")
    assert _store_count(api_url) == 0


@pytest.mark.asyncio
async def test_invalid_approval_token_is_refused(clean_action_store: str, tmp_path: Path) -> None:
    api_url = clean_action_store
    async with _client(_env(tmp_path, api_url)) as client:
        result = await client.call_tool(
            "apply_incident_plan",
            {"incident_id": INCIDENT, "action_id": ACTION, "approval_token": "apv_forged"},
        )
    payload = result.data
    assert payload["status"] == "approval_rejected"
    assert payload["reason"] == "unknown_approval_token"
    assert _store_count(api_url) == 0


@pytest.mark.asyncio
async def test_token_is_bound_to_the_approved_action(
    clean_action_store: str, tmp_path: Path
) -> None:
    api_url = clean_action_store
    async with _client(_env(tmp_path, api_url)) as client:
        pending = await client.call_tool(
            "apply_incident_plan", {"incident_id": INCIDENT, "action_id": ACTION}
        )
        token = pending.data["approval_token"]
        result = await client.call_tool(
            "apply_incident_plan",
            {
                "incident_id": INCIDENT,
                "action_id": "RB-PAY-GATEWAY-01-S3",
                "approval_token": token,
            },
        )
    assert result.data["status"] == "approval_rejected"
    assert result.data["reason"] == "approval_token_bound_to_different_action"
    assert _store_count(api_url) == 0


@pytest.mark.asyncio
async def test_approved_call_applies_exactly_once(clean_action_store: str, tmp_path: Path) -> None:
    api_url = clean_action_store
    async with _client(_env(tmp_path, api_url)) as client:
        pending = await client.call_tool(
            "apply_incident_plan", {"incident_id": INCIDENT, "action_id": ACTION}
        )
        token = pending.data["approval_token"]
        applied = await client.call_tool(
            "apply_incident_plan",
            {"incident_id": INCIDENT, "action_id": ACTION, "approval_token": token},
        )
        again = await client.call_tool(
            "apply_incident_plan",
            {"incident_id": INCIDENT, "action_id": ACTION, "approval_token": token},
        )
    assert applied.data["status"] == "applied"
    assert applied.data["replayed"] is False
    assert again.data["status"] == "replayed"
    assert again.data["side_effect"]["record_id"] == applied.data["side_effect"]["record_id"]
    assert _store_count(api_url) == 1


@pytest.mark.asyncio
async def test_forced_failure_then_resume_leaves_one_side_effect(
    clean_action_store: str, tmp_path: Path
) -> None:
    """The central idempotency proof.

    The injected fault commits the record and THEN returns 500, which is exactly
    the case where a naive retry double-applies. The resume must observe a replay,
    not a second record.
    """
    api_url = clean_action_store
    run_id = "resume-proof"

    async with _client(_env(tmp_path, api_url, run_id=run_id)) as client:
        pending = await client.call_tool(
            "apply_incident_plan", {"incident_id": INCIDENT, "action_id": ACTION}
        )
        token = pending.data["approval_token"]

    async with _client(
        _env(tmp_path, api_url, inject="error_after_commit", run_id=run_id)
    ) as client:
        failed = await client.call_tool(
            "apply_incident_plan",
            {"incident_id": INCIDENT, "action_id": ACTION, "approval_token": token},
        )

    assert failed.data["status"] == "error"
    assert failed.data["error"]["code"] == "upstream_5xx"
    assert failed.data["resume"]["resumable"] is True
    assert failed.data["resume"]["approval_token"] == token
    # The fault committed the record server-side even though the caller saw a 5xx.
    assert _store_count(api_url) == 1

    async with _client(_env(tmp_path, api_url, run_id=run_id)) as client:
        resumed = await client.call_tool(
            "apply_incident_plan",
            {"incident_id": INCIDENT, "action_id": ACTION, "approval_token": token},
        )

    assert resumed.data["status"] == "replayed"
    assert resumed.data["replayed"] is True
    assert _store_count(api_url) == 1, "resume must not create a second side effect"

    events = AuditLog(path=tmp_path / "audit.jsonl", run_id=run_id).events_for_run(run_id)
    kinds = [event["event"] for event in events]
    assert "approval_requested" in kinds
    assert "approval_granted" in kinds
    assert "mutation_failed" in kinds
    assert "mutation_replayed" in kinds
    assert kinds.count("mutation_committed") == 0, "the failing attempt never reported a commit"


@pytest.mark.asyncio
async def test_read_only_credential_cannot_mutate(clean_action_store: str, tmp_path: Path) -> None:
    api_url = clean_action_store
    env = _env(tmp_path, api_url, write_token=None)
    async with _client(env) as client:
        pending = await client.call_tool(
            "apply_incident_plan", {"incident_id": INCIDENT, "action_id": ACTION}
        )
        result = await client.call_tool(
            "apply_incident_plan",
            {
                "incident_id": INCIDENT,
                "action_id": ACTION,
                "approval_token": pending.data["approval_token"],
            },
        )
    payload = result.data
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "auth_failure"
    assert payload["credential_scope"] == "read_token_only"
    assert _store_count(api_url) == 0
