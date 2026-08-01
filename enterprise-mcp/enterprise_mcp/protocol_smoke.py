"""FastMCP protocol smoke with real credential injection.

The previous shell-based smoke used `fastmcp call --command ...`, which cannot
pass environment variables to the spawned server. MCP stdio forwards only
['HOME','LOGNAME','PATH','SHELL','USER'], so the token never arrived and the
server used a hardcoded default. Every credential assertion therefore passed for
the wrong reason. This module passes env explicitly and includes the negative
test that the old smoke could not fail.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

REPO_ROOT = Path(__file__).resolve().parents[2]


def _env(api_url: str, token: str, enabled: str, audit_path: Path, run_id: str) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "PYTHONPATH": f"{REPO_ROOT / 'enterprise-mcp'}:{REPO_ROOT / 'workflow-runner'}",
        "FASTMCP_LOG_LEVEL": "WARNING",
        "ENTERPRISE_API_URL": api_url,
        "ENTERPRISE_API_TOKEN": token,
        "ENTERPRISE_MCP_ENABLED_TOOLS": enabled,
        "AUDIT_LOG_PATH": str(audit_path),
        "APPROVAL_STORE_PATH": str(audit_path.parent / "smoke-approvals.json"),
        "AUDIT_RUN_ID": run_id,
    }


def _client(env: dict[str, str]) -> Client:
    return Client(
        StdioTransport(
            command=sys.executable,
            args=["-m", "enterprise_mcp.server"],
            env=env,
            cwd=str(REPO_ROOT),
        )
    )


async def run_protocol_smoke(
    api_url: str, token: str, incident_id: str, audit_path: Path
) -> dict[str, Any]:
    run_id = "mcp-protocol-smoke"
    results: dict[str, Any] = {"status": "passed", "checks": []}

    def check(name: str, ok: bool, detail: Any) -> None:
        results["checks"].append({"check": name, "ok": bool(ok), "detail": detail})
        if not ok:
            results["status"] = "failed"

    env = _env(api_url, token, "all", audit_path, run_id)
    async with _client(env) as client:
        tools = sorted(tool.name for tool in await client.list_tools())
        check("tool_list", len(tools) == 4, tools)

        health = (await client.call_tool("check_enterprise_api", {})).data
        check("check_enterprise_api", health.get("outcome") == "success", health.get("outcome"))
        results["check_enterprise_api"] = health

        context = (await client.call_tool("get_incident_context", {"incident_id": incident_id})).data
        check("get_incident_context", context.get("outcome") == "success", context.get("outcome"))
        results["get_incident_context"] = context

        plan = (await client.call_tool("propose_incident_plan", {"incident_id": incident_id})).data
        check(
            "propose_incident_plan",
            plan.get("outcome") == "success" and plan.get("approval_required") is True,
            {"outcome": plan.get("outcome"), "approval_required": plan.get("approval_required")},
        )
        results["propose_incident_plan"] = plan

    # Negative: a wrong token must fail. Before the credential fix this passed
    # with a SUCCESS payload because the token never reached the server.
    bad_env = _env(api_url, "wrong-token-negative-control", "all", audit_path, run_id)
    async with _client(bad_env) as client:
        bad = (await client.call_tool("get_incident_context", {"incident_id": incident_id})).data
    check(
        "wrong_token_is_rejected",
        bad.get("outcome") == "error" and bad.get("error", {}).get("code") == "auth_failure",
        bad.get("error"),
    )
    results["negative_control"] = {"outcome": bad.get("outcome"), "error": bad.get("error")}

    # Restricted surface: the mutating tool must be absent when not allowlisted.
    restricted_env = _env(
        api_url, token, "check_enterprise_api,get_incident_context", audit_path, run_id
    )
    async with _client(restricted_env) as client:
        restricted = sorted(tool.name for tool in await client.list_tools())
    check(
        "allowlist_restricts_surface",
        restricted == ["check_enterprise_api", "get_incident_context"],
        restricted,
    )
    results["restricted_tool_list"] = restricted

    blob = json.dumps(results)
    check("token_not_echoed", token not in blob, "token absent from protocol output")

    results["timestamp"] = datetime.now(timezone.utc).isoformat()
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FastMCP protocol smoke with explicit credentials")
    parser.add_argument("--api-url", default=os.environ.get("ENTERPRISE_API_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--token", default=os.environ.get("ENTERPRISE_API_TOKEN", ""))
    parser.add_argument("--incident-id", default=os.environ.get("DEFAULT_INCIDENT_ID", "INC-2026-0042"))
    parser.add_argument("--audit-log", default=os.environ.get("AUDIT_LOG_PATH", str(REPO_ROOT / ".audit" / "smoke-audit.jsonl")))
    parser.add_argument("--output", default=str(REPO_ROOT / ".mcp-receipts" / "fastmcp-protocol.json"))
    args = parser.parse_args(argv)

    if not args.token:
        print("ERROR: ENTERPRISE_API_TOKEN / --token is required (fail closed).", file=sys.stderr)
        return 2

    audit_path = Path(args.audit_log)
    audit_path.parent.mkdir(parents=True, exist_ok=True)

    results = asyncio.run(
        run_protocol_smoke(args.api_url.rstrip("/"), args.token, args.incident_id, audit_path)
    )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")

    print(json.dumps({"status": results["status"], "checks": results["checks"]}, indent=2))
    return 0 if results["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
