from __future__ import annotations

import time
from typing import Any

from workflow_runner.client import EnterpriseApiClient
from workflow_runner.errors import WorkflowError
from workflow_runner.models import DependencyCall, ProposedAction, WorkflowReceipt

LIMITATIONS = [
    "This lab never executes consequential actions against external systems.",
    "Proposed actions are advisory only and require human approval.",
    "Fixture data covers a single deterministic incident for smoke and contract tests.",
]


def build_proposed_actions(incident: dict[str, Any], runbook: dict[str, Any]) -> list[ProposedAction]:
    actions: list[ProposedAction] = [
        ProposedAction(
            order=1,
            description=(
                f"Classify incident {incident['incident_id']} "
                f"({incident.get('severity', 'unknown')} severity) for {incident.get('affected_service', 'unknown service')}"
            ),
            approval_required=False,
            source="workflow-runner",
        ),
        ProposedAction(
            order=2,
            description=f"Review runbook {runbook.get('runbook_id', 'unknown')} and confirm current monitor state",
            approval_required=False,
            source=runbook.get("runbook_id", "runbook"),
        ),
    ]

    for step in runbook.get("steps", []):
        actions.append(
            ProposedAction(
                order=len(actions) + 1,
                description=step["action"],
                approval_required=bool(step.get("approval_required", True)),
                source=runbook.get("runbook_id", "runbook"),
            )
        )

    return actions


def run_incident_intake(
    client: EnterpriseApiClient,
    incident_id: str,
) -> WorkflowReceipt:
    started = time.perf_counter()
    dependency_calls: list[DependencyCall] = []

    try:
        incident, incident_call = client.get_incident(incident_id)
        dependency_calls.append(incident_call)

        runbook, runbook_call = client.get_runbook(incident_id)
        dependency_calls.append(runbook_call)

        proposed_actions = build_proposed_actions(incident, runbook)
        approval_required = any(action.approval_required for action in proposed_actions)

        return WorkflowReceipt(
            correlation_id=client.correlation_id,
            incident_id=incident_id,
            outcome="success",
            approval_required=approval_required,
            proposed_actions=proposed_actions,
            dependency_calls=dependency_calls,
            timing_ms={"total": round((time.perf_counter() - started) * 1000, 2)},
            limitations=LIMITATIONS,
        )
    except WorkflowError as exc:
        return WorkflowReceipt(
            correlation_id=exc.correlation_id or client.correlation_id,
            incident_id=incident_id,
            outcome="error",
            approval_required=True,
            dependency_calls=dependency_calls,
            timing_ms={"total": round((time.perf_counter() - started) * 1000, 2)},
            limitations=LIMITATIONS,
            error={"code": exc.code.value, "message": exc.message},
        )
