"""Approval-gated, idempotent execution of a single runbook action.

Control flow, in one place so it is auditable:

    no approval_token  -> mint token, write NOTHING, return pending_approval
    bad approval_token -> write NOTHING, return approval_rejected
    good approval_token-> POST the mutation with the approval's idempotency key
                          success  -> applied  (new side effect)
                          replay   -> replayed (no new side effect)
                          failure  -> error + resume instructions (same token/key)

The idempotency key is minted with the approval, not with the attempt, so a
resume after a mid-flight failure reuses it automatically.
"""

from __future__ import annotations

import time
from typing import Any

from workflow_runner.approvals import ApprovalStore
from workflow_runner.audit import AuditLog
from workflow_runner.client import EnterpriseApiClient
from workflow_runner.errors import WorkflowError, WorkflowErrorCode
from workflow_runner.models import DependencyCall

STATUS_PENDING_APPROVAL = "pending_approval"
STATUS_APPROVAL_REJECTED = "approval_rejected"
STATUS_APPLIED = "applied"
STATUS_REPLAYED = "replayed"
STATUS_ERROR = "error"

APPLY_LIMITATIONS = [
    "The mutation target is a fixture action store inside the lab API, not a real system.",
    "Approval is enforced in this workflow layer; it is not a Hermes-side policy control.",
    "A resume reuses the approval's idempotency key, so the API returns the original record.",
]


def _serialize(calls: list[DependencyCall]) -> list[dict[str, Any]]:
    return [call.model_dump() for call in calls]


def _resolve_action(runbook: dict[str, Any], action_id: str) -> dict[str, Any] | None:
    for step in runbook.get("steps", []):
        if step.get("step_id") == action_id:
            return step
    return None


def apply_incident_action(
    client: EnterpriseApiClient,
    incident_id: str,
    action_id: str,
    approval_token: str | None = None,
    note: str | None = None,
    inject: str | None = None,
    approvals: ApprovalStore | None = None,
    audit: AuditLog | None = None,
) -> dict[str, Any]:
    approvals = approvals or ApprovalStore()
    audit = audit or AuditLog()
    started = time.perf_counter()
    dependency_calls: list[DependencyCall] = []

    def finish(payload: dict[str, Any]) -> dict[str, Any]:
        payload["dependency_calls"] = _serialize(dependency_calls)
        payload["timing_ms"] = {"total": round((time.perf_counter() - started) * 1000, 2)}
        payload["limitations"] = APPLY_LIMITATIONS
        return payload

    # Resolve the action against the live runbook first: an action that is not in
    # the runbook can never be applied, approved or not.
    try:
        runbook, runbook_call = client.get_runbook(incident_id)
        dependency_calls.append(runbook_call)
    except WorkflowError as exc:
        if exc.call is not None:
            dependency_calls.append(exc.call)
        audit.append(
            "mutation_failed",
            correlation_id=exc.correlation_id or client.correlation_id,
            incident_id=incident_id,
            action_id=action_id,
            outcome="runbook_lookup_failed",
            error_code=exc.code.value,
        )
        return finish(
            {
                "status": STATUS_ERROR,
                "incident_id": incident_id,
                "action_id": action_id,
                "correlation_id": exc.correlation_id or client.correlation_id,
                "side_effect": None,
                "error": {"code": exc.code.value, "message": exc.message},
            }
        )

    step = _resolve_action(runbook, action_id)
    if step is None:
        audit.append(
            "approval_rejected",
            correlation_id=client.correlation_id,
            incident_id=incident_id,
            action_id=action_id,
            outcome="unknown_action_id",
        )
        return finish(
            {
                "status": STATUS_APPROVAL_REJECTED,
                "incident_id": incident_id,
                "action_id": action_id,
                "correlation_id": client.correlation_id,
                "side_effect": None,
                "reason": "unknown_action_id",
                "error": {
                    "code": WorkflowErrorCode.NOT_FOUND.value,
                    "message": (
                        f"{action_id} is not a step in runbook "
                        f"{runbook.get('runbook_id', 'unknown')}"
                    ),
                },
            }
        )

    # --- Gate 1: no token means no write, full stop. -------------------------
    if not approval_token:
        approval = approvals.request(incident_id, action_id, client.correlation_id)
        audit.append(
            "approval_requested",
            correlation_id=client.correlation_id,
            incident_id=incident_id,
            action_id=action_id,
            idempotency_key=approval.idempotency_key,
            outcome="blocked_pending_human_approval",
        )
        return finish(
            {
                "status": STATUS_PENDING_APPROVAL,
                "incident_id": incident_id,
                "action_id": action_id,
                "action_description": step.get("action"),
                "correlation_id": client.correlation_id,
                "side_effect": None,
                "approval_token": approval.approval_token,
                "idempotency_key": approval.idempotency_key,
                "next_step": (
                    "A human must review this action and re-invoke the tool with "
                    "approval_token to execute it. No change has been made."
                ),
            }
        )

    # --- Gate 2: the token must be real and bound to this exact action. ------
    approval, rejection = approvals.validate(approval_token, incident_id, action_id)
    if approval is None:
        audit.append(
            "approval_rejected",
            correlation_id=client.correlation_id,
            incident_id=incident_id,
            action_id=action_id,
            outcome=rejection or "invalid_approval_token",
        )
        return finish(
            {
                "status": STATUS_APPROVAL_REJECTED,
                "incident_id": incident_id,
                "action_id": action_id,
                "correlation_id": client.correlation_id,
                "side_effect": None,
                "reason": rejection,
                "next_step": "Request a fresh approval token by calling without one.",
            }
        )

    audit.append(
        "approval_granted",
        correlation_id=client.correlation_id,
        incident_id=incident_id,
        action_id=action_id,
        idempotency_key=approval.idempotency_key,
        outcome="approved",
    )
    audit.append(
        "mutation_attempted",
        correlation_id=client.correlation_id,
        incident_id=incident_id,
        action_id=action_id,
        idempotency_key=approval.idempotency_key,
        outcome="dispatching",
        inject=inject,
    )

    try:
        payload, call = client.apply_action(
            incident_id=incident_id,
            action_id=action_id,
            idempotency_key=approval.idempotency_key,
            note=note,
            inject=inject,
        )
        dependency_calls.append(call)
    except WorkflowError as exc:
        if exc.call is not None:
            dependency_calls.append(exc.call)
        approvals.record_attempt(
            approval.approval_token,
            "failed",
            {"error_code": exc.code.value, "correlation_id": exc.correlation_id},
        )
        audit.append(
            "mutation_failed",
            correlation_id=exc.correlation_id or client.correlation_id,
            incident_id=incident_id,
            action_id=action_id,
            idempotency_key=approval.idempotency_key,
            outcome="failed",
            error_code=exc.code.value,
            resumable=True,
        )
        return finish(
            {
                "status": STATUS_ERROR,
                "incident_id": incident_id,
                "action_id": action_id,
                "correlation_id": exc.correlation_id or client.correlation_id,
                "side_effect": None,
                "error": {"code": exc.code.value, "message": exc.message},
                "resume": {
                    "resumable": True,
                    "approval_token": approval.approval_token,
                    "idempotency_key": approval.idempotency_key,
                    "instruction": (
                        "Re-invoke with the same approval_token. The idempotency key is "
                        "replayed, so if the write already committed no second side "
                        "effect is created."
                    ),
                },
            }
        )

    replayed = bool(payload.get("replayed"))
    record = payload.get("record", {})
    approvals.record_attempt(
        approval.approval_token,
        "replayed" if replayed else "committed",
        {"record_id": record.get("record_id"), "correlation_id": client.correlation_id},
    )
    approvals.mark_applied(approval.approval_token, record.get("record_id", ""))
    audit.append(
        "mutation_replayed" if replayed else "mutation_committed",
        correlation_id=client.correlation_id,
        incident_id=incident_id,
        action_id=action_id,
        idempotency_key=approval.idempotency_key,
        outcome="replayed" if replayed else "committed",
        record_id=record.get("record_id"),
        total_actions_for_incident=payload.get("total_actions_for_incident"),
    )

    return finish(
        {
            "status": STATUS_REPLAYED if replayed else STATUS_APPLIED,
            "incident_id": incident_id,
            "action_id": action_id,
            "action_description": step.get("action"),
            "correlation_id": client.correlation_id,
            "side_effect": record,
            "replayed": replayed,
            "idempotency_key": approval.idempotency_key,
            "total_actions_for_incident": payload.get("total_actions_for_incident"),
        }
    )
