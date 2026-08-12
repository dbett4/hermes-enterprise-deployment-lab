"""Operator-approved, idempotent execution of a single runbook action.

The mutating caller can request approval but cannot approve its own request.
The separate operator command grants an expiring, single-purpose capability.
See ``workflow_runner.approvals`` for the state machine and threat boundary.

Control flow, in one place so it is auditable:

    no capability      -> mint approval ID, write NOTHING, return pending_approval
    bad capability     -> write NOTHING, return approval_rejected
    good capability    -> POST the mutation with the approval's idempotency key
                          success  -> applied  (new side effect)
                          replay   -> replayed (no new side effect)
                          failure  -> error + resume instructions (same capability/key)

The idempotency key is minted with the approval, not with the attempt, so a
resume after a mid-flight failure reuses it automatically.
"""

from __future__ import annotations

import time
from typing import Any

from opentelemetry.trace import SpanKind, Status, StatusCode

from workflow_runner.approvals import ApprovalStore
from workflow_runner.audit import AuditLog
from workflow_runner.client import EnterpriseApiClient
from workflow_runner.errors import WorkflowError, WorkflowErrorCode
from workflow_runner.models import DependencyCall
from workflow_runner.tracing import add_bounded_event, ensure_tracing, get_tracer

STATUS_PENDING_APPROVAL = "pending_approval"
STATUS_APPROVAL_REJECTED = "approval_rejected"
STATUS_APPLIED = "applied"
STATUS_REPLAYED = "replayed"
STATUS_ERROR = "error"

APPLY_LIMITATIONS = [
    "The mutation target is a fixture action store inside the lab API, not a real system.",
    "The approval gate is enforced in this workflow layer; it is not a Hermes-side policy control.",
    "Approval is granted through a separate local operator command with a recorded identity; "
    "the lab does not integrate a production identity provider or policy engine.",
    "The local JSON approval store uses single-host fcntl file locks; it is not a distributed production datastore.",
    "A resume reuses the approval's idempotency key, so the API returns the original record.",
    "Deduplication is keyed on the idempotency key, which is minted per approval: two approvals "
    "for the same action_id produce two records.",
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
    approval_capability: str | None = None,
    note: str | None = None,
    inject: str | None = None,
    approvals: ApprovalStore | None = None,
    audit: AuditLog | None = None,
) -> dict[str, Any]:
    approvals = approvals or ApprovalStore()
    audit = audit or AuditLog()
    ensure_tracing()
    with get_tracer().start_as_current_span(
        "apply_incident_action",
        kind=SpanKind.INTERNAL,
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        return _apply_incident_action(
            client,
            incident_id,
            action_id,
            span,
            approval_capability=approval_capability,
            note=note,
            inject=inject,
            approvals=approvals,
            audit=audit,
        )


def _apply_incident_action(
    client: EnterpriseApiClient,
    incident_id: str,
    action_id: str,
    span: Any,
    approval_capability: str | None = None,
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
        span.set_status(Status(StatusCode.ERROR))
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
        add_bounded_event(span, "approval.rejected", {"reason": "unknown_action_id"})
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

    # --- Gate 1: the caller can request approval, but receives no secret. ----
    if not approval_capability:
        approval = approvals.request(incident_id, action_id, client.correlation_id)
        audit.append(
            "approval_requested",
            correlation_id=client.correlation_id,
            incident_id=incident_id,
            action_id=action_id,
            approval_id=approval.approval_id,
            outcome="blocked_pending_operator_approval",
        )
        add_bounded_event(span, "approval.requested")
        return finish(
            {
                "status": STATUS_PENDING_APPROVAL,
                "incident_id": incident_id,
                "action_id": action_id,
                "action_description": step.get("action"),
                "correlation_id": client.correlation_id,
                "side_effect": None,
                "approval_id": approval.approval_id,
                "approval_expires_at": approval.expires_at,
                "next_step": (
                    "No change has been made. A separate operator must run "
                    "`python -m workflow_runner.approval_operator approve "
                    f"{approval.approval_id} --approver <identity>` and deliver the "
                    "resulting capability to the caller before it expires."
                ),
            }
        )

    # --- Gate 2: capability must be granted, live, and bound to this action. -
    approval, rejection = approvals.validate(approval_capability, incident_id, action_id)
    if approval is None:
        audit.append(
            "approval_rejected",
            correlation_id=client.correlation_id,
            incident_id=incident_id,
            action_id=action_id,
            outcome=rejection or "invalid_approval_capability",
        )
        add_bounded_event(
            span,
            "approval.rejected",
            {"reason": rejection} if rejection else None,
        )
        return finish(
            {
                "status": STATUS_APPROVAL_REJECTED,
                "incident_id": incident_id,
                "action_id": action_id,
                "correlation_id": client.correlation_id,
                "side_effect": None,
                "reason": rejection,
                "next_step": "Request a fresh approval ID by calling without a capability.",
            }
        )

    audit.append(
        "approval_capability_accepted",
        correlation_id=client.correlation_id,
        incident_id=incident_id,
        action_id=action_id,
        idempotency_key=approval.idempotency_key,
        approval_id=approval.approval_id,
        approved_by=approval.approved_by,
        outcome="authorized",
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

    add_bounded_event(span, "approval.accepted")
    add_bounded_event(span, "mutation.dispatched")

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
            approval.approval_id,
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
        add_bounded_event(span, "mutation.failed_resumable")
        span.set_status(Status(StatusCode.ERROR))
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
                    "approval_id": approval.approval_id,
                    "idempotency_key": approval.idempotency_key,
                    "instruction": (
                        "Re-invoke with the same approval_capability. The idempotency key is "
                        "replayed, so if the write already committed no second side "
                        "effect is created."
                    ),
                },
            }
        )

    replayed = bool(payload.get("replayed"))
    record = payload.get("record", {})
    approvals.record_attempt(
        approval.approval_id,
        "replayed" if replayed else "committed",
        {"record_id": record.get("record_id"), "correlation_id": client.correlation_id},
    )
    approvals.mark_applied(approval.approval_id, record.get("record_id", ""))
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
    add_bounded_event(span, "mutation.replayed" if replayed else "mutation.applied")

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
