"""One-command demo of the full arc, driven entirely over MCP stdio.

Every tool call below crosses a real MCP stdio boundary into a freshly spawned
server process. Nothing here calls the tool functions directly, and nothing here
involves an LLM: the caller is this script, not a model.

Arc:
    1. scoped surface      read/plan allowlist does not expose the mutating tool
    2. discovery           the write-enabled allowlist exposes exactly four tools
    3. read + plan         context and a guarded plan
    4. approval request    caller gets an opaque ID and no capability; no write
    5. operator approval   separate command records identity and grants capability
    6. forced failure      post-commit fault; caller sees 5xx, resume info returned
    7. resume              same capability and idempotency key -> replayed
    8. exactly-once        the store holds exactly one record; grant is terminal
    9. read-only scope     a write attempt with read scope is refused by the API
   10. audit trail         append-only events for the whole run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

import httpx
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from workflow_runner.audit import AuditLog

REPO_ROOT = Path(__file__).resolve().parents[2]
READ_PLAN_TOOLS = "check_enterprise_api,get_incident_context,propose_incident_plan"
ALL_TOOLS = "all"


class DemoFailure(AssertionError):
    """Raised when the demo does not observe the behaviour it claims to prove."""


def _server_env(
    api_url: str,
    read_token: str,
    write_token: str | None,
    enabled_tools: str,
    audit_path: Path,
    approval_path: Path,
    run_id: str,
    inject: str | None = None,
) -> dict[str, str]:
    """Build the FULL environment for the MCP subprocess.

    MCP stdio does not inherit the parent environment: the SDK forwards only a
    small allowlist (HOME, LOGNAME, PATH, SHELL, USER). Anything the server needs
    has to be passed explicitly, which is what this function exists to do.
    """
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "PYTHONPATH": f"{REPO_ROOT / 'enterprise-mcp'}:{REPO_ROOT / 'workflow-runner'}",
        "PYTHONUNBUFFERED": "1",
        # Keep the server's own startup chatter off the demo transcript.
        "FASTMCP_LOG_LEVEL": "WARNING",
        "ENTERPRISE_API_URL": api_url,
        "ENTERPRISE_API_TOKEN": read_token,
        "ENTERPRISE_API_TIMEOUT_SECONDS": os.environ.get("ENTERPRISE_API_TIMEOUT_SECONDS", "10"),
        "ENTERPRISE_MCP_ENABLED_TOOLS": enabled_tools,
        "AUDIT_LOG_PATH": str(audit_path),
        "APPROVAL_STORE_PATH": str(approval_path),
        "AUDIT_RUN_ID": run_id,
    }
    if write_token:
        env["ENTERPRISE_API_WRITE_TOKEN"] = write_token
    if inject:
        env["ENTERPRISE_INJECT_FAILURE"] = inject
    return env


@asynccontextmanager
async def _session(env: dict[str, str]):
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "enterprise_mcp.server"],
        env=env,
        cwd=str(REPO_ROOT),
    )
    async with Client(transport) as client:
        yield client


def _unwrap(result: Any) -> dict[str, Any]:
    data = getattr(result, "data", None)
    return data if isinstance(data, dict) else {"raw": str(result)}


async def run_demo(
    api_url: str,
    read_token: str,
    write_token: str,
    audit_path: Path,
    approval_path: Path,
    run_id: str | None = None,
    emit: Callable[[str], None] = print,
) -> dict[str, Any]:
    run_id = run_id or f"demo-{uuid.uuid4()}"
    audit = AuditLog(path=audit_path, run_id=run_id)
    steps: list[dict[str, Any]] = []

    def step(number: int, title: str) -> None:
        emit("")
        emit(f"--- STEP {number}: {title} " + "-" * max(0, 52 - len(title)))

    def show(label: str, payload: Any) -> None:
        emit(f"{label}: {json.dumps(payload, indent=2, sort_keys=True, default=str)}")

    def record(name: str, **fields: Any) -> None:
        steps.append({"step": name, **fields})

    incident_id = os.environ.get("DEFAULT_INCIDENT_ID", "INC-2026-0042")
    action_id = os.environ.get("DEMO_ACTION_ID", "RB-PAY-GATEWAY-01-S2")

    read_headers = {"Authorization": f"Bearer {read_token}"}
    write_headers = {"Authorization": f"Bearer {write_token}"}

    def store_count() -> int:
        response = httpx.get(
            f"{api_url}/v1/incidents/{incident_id}/actions", headers=read_headers, timeout=10
        )
        response.raise_for_status()
        return int(response.json()["count"])

    emit("=" * 72)
    emit("HERMES ENTERPRISE DEPLOYMENT LAB - SEPARATED APPROVAL / IDEMPOTENCY / RESUME")
    emit("=" * 72)
    emit(f"run_id       : {run_id}")
    emit(f"enterprise   : {api_url}")
    emit(f"incident     : {incident_id}")
    emit(f"action       : {action_id}")
    emit(f"audit log    : {audit_path}")
    emit("caller       : this script over MCP stdio (no LLM in the loop, ever)")

    audit.append("run_started", incident_id=incident_id, action_id=action_id, actor="demo")

    # Deterministic start: clear the fixture store.
    reset = httpx.post(f"{api_url}/v1/admin/reset-actions", headers=write_headers, timeout=10)
    reset.raise_for_status()

    base_env = dict(
        api_url=api_url,
        read_token=read_token,
        write_token=write_token,
        audit_path=audit_path,
        approval_path=approval_path,
        run_id=run_id,
    )

    def operator_approve(approval_id: str, approver: str) -> str:
        operator_env = os.environ.copy()
        operator_env.update(
            {
                "PYTHONPATH": f"{REPO_ROOT / 'workflow-runner'}",
                "APPROVAL_STORE_PATH": str(approval_path),
                "AUDIT_LOG_PATH": str(audit_path),
                "AUDIT_RUN_ID": run_id,
            }
        )
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "workflow_runner.approval_operator",
                "approve",
                approval_id,
                "--approver",
                approver,
            ],
            cwd=REPO_ROOT,
            env=operator_env,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise DemoFailure(
                f"operator approval failed ({completed.returncode}): {completed.stderr or completed.stdout}"
            )
        response = json.loads(completed.stdout)
        if response.get("status") != "approved":
            raise DemoFailure(f"operator approval was not granted: {response}")
        return str(response["approval_capability"])

    # 1. Scoped surface -----------------------------------------------------
    step(1, "scoped permissions: read/plan allowlist")
    async with _session(_server_env(enabled_tools=READ_PLAN_TOOLS, **base_env)) as client:
        restricted = sorted(tool.name for tool in await client.list_tools())
        show("tools visible", restricted)
        if "apply_incident_plan" in restricted:
            raise DemoFailure("mutating tool exposed under the read/plan allowlist")
        try:
            await client.call_tool("apply_incident_plan", {"incident_id": incident_id, "action_id": action_id})
            raise DemoFailure("excluded tool was callable")
        except DemoFailure:
            raise
        except Exception as exc:  # noqa: BLE001 - the refusal is the proof
            emit(f"calling the excluded tool failed as required: {type(exc).__name__}: {exc}")
            record("scoped_surface", visible=restricted, excluded_call_error=type(exc).__name__)

    # 2. Full surface -------------------------------------------------------
    step(2, "discovery: write-enabled allowlist")
    write_env = _server_env(enabled_tools=ALL_TOOLS, **base_env)
    async with _session(write_env) as client:
        full = sorted(tool.name for tool in await client.list_tools())
        show("tools visible", full)
        if "apply_incident_plan" not in full:
            raise DemoFailure("mutating tool missing under the write-enabled allowlist")
        record("full_surface", visible=full)

        # 3. Read + plan ----------------------------------------------------
        step(3, "read and plan")
        health = _unwrap(await client.call_tool("check_enterprise_api", {}))
        show("check_enterprise_api", {k: health[k] for k in ("outcome", "base_url", "mutation_capability")})
        plan = _unwrap(await client.call_tool("propose_incident_plan", {"incident_id": incident_id}))
        show(
            "propose_incident_plan",
            {
                "outcome": plan.get("outcome"),
                "approval_required": plan.get("approval_required"),
                "proposed_actions": [
                    {
                        "order": a.get("order"),
                        "action_id": a.get("action_id"),
                        "approval_required": a.get("approval_required"),
                        "description": a.get("description"),
                    }
                    for a in plan.get("proposed_actions", [])
                ],
            },
        )
        record("plan", outcome=plan.get("outcome"), approval_required=plan.get("approval_required"))

        # 4. Separated approval request -------------------------------------
        step(4, "approval request: caller receives no capability")
        before = store_count()
        pending = _unwrap(
            await client.call_tool(
                "apply_incident_plan", {"incident_id": incident_id, "action_id": action_id}
            )
        )
        after = store_count()
        show(
            "apply_incident_plan (no capability)",
            {
                "status": pending.get("status"),
                "side_effect": pending.get("side_effect"),
                "approval_id": pending.get("approval_id"),
                "approval_capability_present": "approval_capability" in pending,
                "idempotency_key_present": "idempotency_key" in pending,
                "next_step": pending.get("next_step"),
            },
        )
        emit(f"side effects in store: before={before} after={after}")
        if pending.get("status") != "pending_approval":
            raise DemoFailure(f"expected pending_approval, got {pending.get('status')}")
        if before != after:
            raise DemoFailure("un-approved call changed the store")
        if "approval_capability" in pending or "approval_token" in pending:
            raise DemoFailure("the request response leaked an approval secret")
        approval_id = pending["approval_id"]
        record(
            "approval_request",
            status=pending.get("status"),
            store_before=before,
            store_after=after,
            approval_id=approval_id,
            capability_returned_to_caller=False,
        )

    # 5. Separate operator approval ----------------------------------------
    step(5, "operator approval: separate command + recorded identity")
    approver = "demo-operator@example.com"
    approval_capability = operator_approve(approval_id, approver)
    emit(f"operator command approved {approval_id} as {approver}")
    emit("capability delivered to caller: yes (value redacted from transcript and store)")
    record(
        "operator_approval",
        approval_id=approval_id,
        approver=approver,
        capability_redacted=True,
    )

    # 6. Forced failure -----------------------------------------------------
    step(6, "forced failure: post-commit fault (ENTERPRISE_INJECT_FAILURE=error_after_commit)")
    fault_env = _server_env(enabled_tools=ALL_TOOLS, inject="error_after_commit", **base_env)
    async with _session(fault_env) as client:
        failed = _unwrap(
            await client.call_tool(
                "apply_incident_plan",
                {
                    "incident_id": incident_id,
                    "action_id": action_id,
                    "approval_capability": approval_capability,
                },
            )
        )
    show(
        "apply_incident_plan (approved, fault injected)",
        {"status": failed.get("status"), "error": failed.get("error"), "resume": failed.get("resume")},
    )
    emit(f"side effects in store after the failed call: {store_count()}")
    if failed.get("status") != "error":
        raise DemoFailure(f"expected error, got {failed.get('status')}")
    record("forced_failure", status=failed.get("status"), error=failed.get("error"))

    # 7/8. Resume + exactly-once -------------------------------------------
    step(7, "resume with the same approval capability")
    async with _session(write_env) as client:
        resumed = _unwrap(
            await client.call_tool(
                "apply_incident_plan",
                {
                    "incident_id": incident_id,
                    "action_id": action_id,
                    "approval_capability": approval_capability,
                },
            )
        )
    show(
        "apply_incident_plan (resume)",
        {
            "status": resumed.get("status"),
            "replayed": resumed.get("replayed"),
            "idempotency_key": resumed.get("idempotency_key"),
            "side_effect": resumed.get("side_effect"),
        },
    )

    step(8, "exactly-once + terminal approval check")
    final_count = store_count()
    emit(f"records in the enterprise action store: {final_count}")
    if final_count != 1:
        raise DemoFailure(f"expected exactly one side effect, found {final_count}")
    if resumed.get("status") != "replayed":
        raise DemoFailure(f"expected replayed, got {resumed.get('status')}")
    async with _session(write_env) as client:
        terminal = _unwrap(
            await client.call_tool(
                "apply_incident_plan",
                {
                    "incident_id": incident_id,
                    "action_id": action_id,
                    "approval_capability": approval_capability,
                },
            )
        )
    emit(
        "third use after terminal apply: "
        f"status={terminal.get('status')} reason={terminal.get('reason')}"
    )
    if terminal.get("reason") != "approval_already_applied":
        raise DemoFailure(f"expected terminal approval refusal, got {terminal}")
    if store_count() != 1:
        raise DemoFailure("terminal capability dispatched another side effect")
    record("resume", status=resumed.get("status"), store_count=final_count)

    # 9. Read-only scope ----------------------------------------------------
    step(9, "read-only credential cannot mutate")
    read_only_env = _server_env(
        api_url=api_url,
        read_token=read_token,
        write_token=None,
        enabled_tools=ALL_TOOLS,
        audit_path=audit_path,
        approval_path=approval_path,
        run_id=run_id,
    )
    async with _session(read_only_env) as client:
        pending_ro = _unwrap(
            await client.call_tool(
                "apply_incident_plan", {"incident_id": incident_id, "action_id": "RB-PAY-GATEWAY-01-S3"}
            )
        )
        read_only_approval_id = pending_ro["approval_id"]
    read_only_capability = operator_approve(read_only_approval_id, approver)
    async with _session(read_only_env) as client:
        refused = _unwrap(
            await client.call_tool(
                "apply_incident_plan",
                {
                    "incident_id": incident_id,
                    "action_id": "RB-PAY-GATEWAY-01-S3",
                    "approval_capability": read_only_capability,
                },
            )
        )
    show(
        "apply_incident_plan (approved, read-only credential)",
        {
            "status": refused.get("status"),
            "error": refused.get("error"),
            "credential_scope": refused.get("credential_scope"),
        },
    )
    after_ro = store_count()
    emit(f"records in the enterprise action store: {after_ro}")
    if refused.get("error", {}).get("code") != "auth_failure":
        raise DemoFailure(f"expected auth_failure, got {refused.get('error')}")
    if after_ro != 1:
        raise DemoFailure("read-only credential produced a side effect")
    record("read_only_scope", status=refused.get("status"), store_count=after_ro)

    # 10. Audit -------------------------------------------------------------
    step(10, "append-only audit trail for this run")
    events = audit.events_for_run(run_id)
    for event in events:
        emit(
            f"  [{event['seq']:>3}] {event['event']:<20} "
            f"actor={event.get('actor'):<16} "
            f"outcome={str(event.get('outcome')):<28} "
            f"corr={str(event.get('correlation_id'))[:8]}"
        )
    audit.append("run_finished", outcome="passed", actor="demo", store_count=final_count)

    emit("")
    emit("=" * 72)
    emit(
        "DEMO PASSED - separated approval enforced, failure survived, "
        "exactly one side effect, capability terminal"
    )
    emit("=" * 72)

    return {
        "demo_status": "passed",
        "run_id": run_id,
        "incident_id": incident_id,
        "action_id": action_id,
        "final_store_count": final_count,
        "audit_events": len(events) + 1,
        "audit_log_path": str(audit_path),
        "steps": steps,
        "not_proven": [
            "No LLM chose these tools; the caller is this script.",
            "Hermes model-driven invocation is not exercised here, and is not planned.",
            "The demo automates both roles: it invokes the separate operator command with "
            "a fixture identity; it does not prove a real person's identity or judgment.",
            "The approval store is a local JSON file, not a production identity/policy service.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the approval/idempotency/resume demo")
    parser.add_argument("--api-url", default=os.environ.get("ENTERPRISE_API_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--read-token", default=os.environ.get("ENTERPRISE_API_TOKEN", ""))
    parser.add_argument("--write-token", default=os.environ.get("ENTERPRISE_API_WRITE_TOKEN", ""))
    parser.add_argument("--audit-log", default=os.environ.get("AUDIT_LOG_PATH", str(REPO_ROOT / ".audit" / "demo-audit.jsonl")))
    parser.add_argument("--approval-store", default=os.environ.get("APPROVAL_STORE_PATH", str(REPO_ROOT / ".audit" / "demo-approvals.json")))
    parser.add_argument("--receipt", default=os.environ.get("DEMO_RECEIPT_PATH", str(REPO_ROOT / ".smoke-receipts" / "demo-receipt.json")))
    args = parser.parse_args(argv)

    if not args.read_token or not args.write_token:
        print(
            "ERROR: --read-token/--write-token (or ENTERPRISE_API_TOKEN / "
            "ENTERPRISE_API_WRITE_TOKEN) are required; this demo fails closed.",
            file=sys.stderr,
        )
        return 2

    audit_path = Path(args.audit_log)
    approval_path = Path(args.approval_store)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    approval_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        result = asyncio.run(
            run_demo(
                api_url=args.api_url.rstrip("/"),
                read_token=args.read_token,
                write_token=args.write_token,
                audit_path=audit_path,
                approval_path=approval_path,
            )
        )
    except DemoFailure as exc:
        print(f"\nDEMO FAILED: {exc}", file=sys.stderr)
        return 1

    receipt_path = Path(args.receipt)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"\nreceipt: {receipt_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
