from __future__ import annotations

from typing import Any

from workflow_runner.client import EnterpriseApiClient
from workflow_runner.errors import WorkflowError
from workflow_runner.models import DependencyCall


def _serialize_calls(calls: list[DependencyCall]) -> list[dict[str, Any]]:
    return [call.model_dump() for call in calls]


def fetch_incident_context(
    client: EnterpriseApiClient,
    incident_id: str,
) -> dict[str, Any]:
    dependency_calls: list[DependencyCall] = []

    try:
        incident, incident_call = client.get_incident(incident_id)
        dependency_calls.append(incident_call)

        runbook, runbook_call = client.get_runbook(incident_id)
        dependency_calls.append(runbook_call)

        return {
            "outcome": "success",
            "correlation_id": client.correlation_id,
            "incident_id": incident_id,
            "incident": incident,
            "runbook": runbook,
            "dependency_calls": _serialize_calls(dependency_calls),
        }
    except WorkflowError as exc:
        return {
            "outcome": "error",
            "correlation_id": exc.correlation_id or client.correlation_id,
            "incident_id": incident_id,
            "incident": None,
            "runbook": None,
            "dependency_calls": _serialize_calls(dependency_calls),
            "error": {"code": exc.code.value, "message": exc.message},
        }
