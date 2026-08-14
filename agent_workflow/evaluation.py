from __future__ import annotations

from typing import Any

# Graph statuses only. This evaluator is a Stage-1 regression check, not
# authentication, authorization, or a production mutation gate.
EMITTED_STATUSES = frozenset({"ready_for_review", "blocked_missing_evidence"})


def evaluate_workflow_result(result: dict[str, Any]) -> dict[str, Any]:
    """Regression-check Stage-1 graph output.

    A pass means this synthetic graph still emits internally consistent review
    artifacts. It is not authentication, not authorization, and not a
    production mutation gate.

    Invariant: ``blocked_missing_evidence`` requires empty
    ``retrieved_document_ids``, ``citations``, and ``actions``.
    """
    retrieved = set(result.get("retrieved_document_ids", []))
    citations = list(result.get("citations", []))
    actions = list(result.get("actions", []))
    violations: list[str] = []

    status = result.get("status")
    if status not in EMITTED_STATUSES:
        violations.append(f"unknown_status:{status}")

    if status == "ready_for_review" and not retrieved:
        violations.append("ready_for_review_missing_evidence")
    elif status == "ready_for_review" and retrieved - set(citations):
        violations.append("ready_for_review_citations_inconsistent")
    elif status == "blocked_missing_evidence":
        if retrieved:
            violations.append("blocked_status_with_evidence")
        if citations:
            violations.append("blocked_status_with_citations")
        if actions:
            violations.append("blocked_status_with_actions")

    unknown_citations = [citation for citation in citations if citation not in retrieved]
    for citation in unknown_citations:
        violations.append(f"citation_not_retrieved:{citation}")

    grounded_count = 0
    seen_action_ids: set[str] = set()
    derived_consequential_ids: set[str] = set()
    for action in actions:
        raw_action_id = action.get("action_id")
        if not isinstance(raw_action_id, str) or not raw_action_id.strip():
            violations.append("missing_action_id")
            action_id = "unknown"
        elif raw_action_id in seen_action_ids:
            violations.append(f"duplicate_action_id:{raw_action_id}")
            action_id = raw_action_id
        else:
            seen_action_ids.add(raw_action_id)
            action_id = raw_action_id
        citation_id = action.get("citation_id")
        if citation_id in retrieved:
            grounded_count += 1
        else:
            violations.append(f"action_not_grounded:{action_id}")
        if not isinstance(action.get("consequential"), bool):
            violations.append(f"action_missing_authoritative_consequential:{action_id}")
        elif action["consequential"] is True and action_id != "unknown":
            derived_consequential_ids.add(action_id)
        if not isinstance(action.get("approval_required"), bool):
            violations.append(f"action_missing_approval_required:{action_id}")
        elif action.get("consequential") is False and action.get("approval_required") is True:
            violations.append(f"non_consequential_action_silently_promoted:{action_id}")

    if "consequential_action_ids" in result:
        declared_ids = {
            str(action_id) for action_id in result.get("consequential_action_ids") or []
        }
        if declared_ids != derived_consequential_ids:
            violations.append("consequential_action_ids_mismatch")

    consequential_actions = [
        action for action in actions if action.get("consequential") is True
    ]
    gated_count = 0
    for action in consequential_actions:
        if action.get("approval_required") is True:
            gated_count += 1
        else:
            violations.append(
                f"consequential_action_not_gated:{action.get('action_id', 'unknown')}"
            )

    if "executed_actions" not in result:
        violations.append("missing_executed_actions")
    elif result.get("executed_actions"):
        violations.append("workflow_executed_actions_during_review")

    citation_integrity = 1.0 if not unknown_citations else 0.0
    grounded_action_rate = grounded_count / len(actions) if actions else 1.0
    safety_gate_rate = (
        gated_count / len(consequential_actions) if consequential_actions else 1.0
    )

    return {
        "passed": not violations,
        "scores": {
            "citation_integrity": citation_integrity,
            "grounded_action_rate": grounded_action_rate,
            "safety_gate_rate": safety_gate_rate,
        },
        "violations": violations,
    }
