"""Regression tests for the credential-injection defect found on 2026-08-01.

Defect: `mcp.client.stdio.get_default_environment()` forwards only
['HOME','LOGNAME','PATH','SHELL','USER'], so ENTERPRISE_API_TOKEN never reached
the MCP subprocess. The server fell back to a hardcoded "lab-read-token"
default, which meant a smoke run with a deliberately wrong token still SUCCEEDED
and every credential assertion passed trivially.

Fix has two halves, both asserted here:
  1. the server fails closed when no token is present, and
  2. the client passes the token explicitly, so a wrong token really fails.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHONPATH = f"{REPO_ROOT / 'enterprise-mcp'}:{REPO_ROOT / 'workflow-runner'}"


def _base_env(tmp_path: Path, api_url: str, token: str | None) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "PYTHONPATH": PYTHONPATH,
        "ENTERPRISE_API_URL": api_url,
        "ENTERPRISE_MCP_ENABLED_TOOLS": "all",
        "AUDIT_LOG_PATH": str(tmp_path / "audit.jsonl"),
        "APPROVAL_STORE_PATH": str(tmp_path / "approvals.json"),
        "AUDIT_RUN_ID": "stdio-credential-test",
    }
    if token is not None:
        env["ENTERPRISE_API_TOKEN"] = token
    return env


def _transport(env: dict[str, str]) -> StdioTransport:
    return StdioTransport(
        command=sys.executable,
        args=["-m", "enterprise_mcp.server"],
        env=env,
        cwd=str(REPO_ROOT),
    )


def test_server_fails_closed_without_a_token(tmp_path: Path) -> None:
    """No token in the process environment must be a hard, visible failure."""
    result = subprocess.run(
        [sys.executable, "-m", "enterprise_mcp.server"],
        cwd=str(REPO_ROOT),
        env=_base_env(tmp_path, "http://127.0.0.1:9", token=None),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "ENTERPRISE_API_TOKEN is not set" in result.stderr


@pytest.mark.asyncio
async def test_correct_token_reaches_the_subprocess(live_api: str, tmp_path: Path) -> None:
    env = _base_env(tmp_path, live_api, token="lab-read-token")
    async with Client(_transport(env)) as client:
        result = await client.call_tool(
            "get_incident_context", {"incident_id": "INC-2026-0042"}
        )
    assert result.data["outcome"] == "success"
    assert result.data["runbook"]["runbook_id"] == "RB-PAY-GATEWAY-01"


@pytest.mark.asyncio
async def test_wrong_token_actually_fails(live_api: str, tmp_path: Path) -> None:
    """The assertion the old smoke could not make: a wrong token must fail."""
    env = _base_env(tmp_path, live_api, token="definitely-not-the-token")
    async with Client(_transport(env)) as client:
        result = await client.call_tool(
            "get_incident_context", {"incident_id": "INC-2026-0042"}
        )
    assert result.data["outcome"] == "error"
    assert result.data["error"]["code"] == "auth_failure"
    assert "definitely-not-the-token" not in str(result.data)


@pytest.mark.asyncio
async def test_enabled_tools_env_is_honoured_across_the_stdio_boundary(
    live_api: str, tmp_path: Path
) -> None:
    env = _base_env(tmp_path, live_api, token="lab-read-token")
    env["ENTERPRISE_MCP_ENABLED_TOOLS"] = "check_enterprise_api,get_incident_context"
    async with Client(_transport(env)) as client:
        names = sorted(tool.name for tool in await client.list_tools())
    assert names == ["check_enterprise_api", "get_incident_context"]
