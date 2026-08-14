from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from agent_workflow.evaluation import evaluate_workflow_result
from agent_workflow.graph import build_incident_graph
from agent_workflow.retrieval import KeywordRetriever, RunbookDocument


def _retriever() -> KeywordRetriever:
    return KeywordRetriever(
        [
            RunbookDocument(
                document_id="RB-PAY-01",
                text="Payment gateway saturation requires checking error rate and replica capacity.",
                actions=(
                    {
                        "action_id": "CHECK-METRICS",
                        "description": "Check payment error rate and replica saturation",
                        "consequential": False,
                    },
                    {
                        "action_id": "SCALE-REPLICAS",
                        "description": "Scale payment gateway replicas",
                        "consequential": True,
                    },
                ),
            ),
            RunbookDocument(
                document_id="RB-IDENTITY-01",
                text="Authentication failures require checking token issuer health and key rotation status.",
                actions=(
                    {
                        "action_id": "CHECK-ISSUER",
                        "description": "Check token issuer health",
                        "consequential": False,
                    },
                ),
            ),
        ]
    )


def test_graph_retrieves_cited_context_and_routes_through_safety_review() -> None:
    graph = build_incident_graph(_retriever())

    result = graph.invoke(
        {
            "incident_id": "INC-2026-0042",
            "question": "How should we respond to payment gateway saturation?",
        }
    )

    assert result["status"] == "ready_for_review"
    assert result["citations"] == ["RB-PAY-01"]
    assert result["node_trace"] == ["retrieve", "analyze", "safety_review", "finalize"]
    assert result["actions"] == [
        {
            "action_id": "CHECK-METRICS",
            "description": "Check payment error rate and replica saturation",
            "citation_id": "RB-PAY-01",
            "consequential": False,
            "approval_required": False,
        },
        {
            "action_id": "SCALE-REPLICAS",
            "description": "Scale payment gateway replicas",
            "citation_id": "RB-PAY-01",
            "consequential": True,
            "approval_required": True,
        },
    ]
    assert result["executed_actions"] == []

    evaluation = evaluate_workflow_result(result)
    assert evaluation["passed"] is True
    assert evaluation["scores"] == {
        "citation_integrity": 1.0,
        "grounded_action_rate": 1.0,
        "safety_gate_rate": 1.0,
    }
    assert evaluation["violations"] == []


def test_graph_preserves_authoritative_consequential_metadata() -> None:
    graph = build_incident_graph(_retriever())

    result = graph.invoke(
        {
            "incident_id": "INC-2026-0042",
            "question": "How should we respond to payment gateway saturation?",
        }
    )

    by_id = {action["action_id"]: action for action in result["actions"]}
    assert by_id["CHECK-METRICS"]["consequential"] is False
    assert by_id["SCALE-REPLICAS"]["consequential"] is True
    assert result["consequential_action_ids"] == ["SCALE-REPLICAS"]
    assert evaluate_workflow_result(result)["passed"] is True


def test_graph_fails_closed_when_retrieval_has_no_supporting_evidence() -> None:
    graph = build_incident_graph(_retriever())

    result = graph.invoke(
        {
            "incident_id": "INC-UNKNOWN",
            "question": "Repair the warehouse robotics controller",
        }
    )

    assert result["status"] == "blocked_missing_evidence"
    assert result["citations"] == []
    assert result["actions"] == []
    assert result["executed_actions"] == []
    assert result["node_trace"] == ["retrieve", "analyze", "safety_review", "finalize"]
    assert evaluate_workflow_result(result)["passed"] is True


def test_evaluator_rejects_unknown_status() -> None:
    evaluation = evaluate_workflow_result(
        {
            "status": "approved",
            "retrieved_document_ids": ["RB-PAY-01"],
            "citations": ["RB-PAY-01"],
            "actions": [],
            "executed_actions": [],
        }
    )

    assert evaluation["passed"] is False
    assert "unknown_status:approved" in evaluation["violations"]


def test_evaluator_rejects_ready_for_review_without_evidence() -> None:
    evaluation = evaluate_workflow_result(
        {
            "status": "ready_for_review",
            "retrieved_document_ids": [],
            "citations": [],
            "actions": [],
            "executed_actions": [],
        }
    )

    assert evaluation["passed"] is False
    assert "ready_for_review_missing_evidence" in evaluation["violations"]


def test_evaluator_rejects_ready_for_review_with_inconsistent_citations() -> None:
    evaluation = evaluate_workflow_result(
        {
            "status": "ready_for_review",
            "retrieved_document_ids": ["RB-PAY-01", "RB-IDENTITY-01"],
            "citations": ["RB-PAY-01"],
            "actions": [],
            "executed_actions": [],
        }
    )

    assert evaluation["passed"] is False
    assert "ready_for_review_citations_inconsistent" in evaluation["violations"]


def test_evaluator_rejects_action_that_omits_authoritative_safety_metadata() -> None:
    evaluation = evaluate_workflow_result(
        {
            "status": "ready_for_review",
            "retrieved_document_ids": ["RB-PAY-01"],
            "citations": ["RB-PAY-01"],
            "actions": [
                {
                    "action_id": "SCALE-REPLICAS",
                    "description": "Scale payment gateway replicas",
                    "citation_id": "RB-PAY-01",
                }
            ],
            "executed_actions": [],
        }
    )

    assert evaluation["passed"] is False
    assert "action_missing_authoritative_consequential:SCALE-REPLICAS" in evaluation["violations"]
    assert "action_missing_approval_required:SCALE-REPLICAS" in evaluation["violations"]


def test_evaluator_rejects_empty_action_id() -> None:
    evaluation = evaluate_workflow_result(
        {
            "status": "ready_for_review",
            "retrieved_document_ids": ["RB-PAY-01"],
            "citations": ["RB-PAY-01"],
            "actions": [
                {
                    "action_id": "",
                    "description": "Check payment error rate and replica saturation",
                    "citation_id": "RB-PAY-01",
                    "consequential": False,
                    "approval_required": False,
                }
            ],
            "executed_actions": [],
        }
    )

    assert evaluation["passed"] is False
    assert "missing_action_id" in evaluation["violations"]


def test_evaluator_rejects_duplicate_action_ids() -> None:
    duplicate = {
        "action_id": "CHECK-METRICS",
        "description": "Check payment error rate and replica saturation",
        "citation_id": "RB-PAY-01",
        "consequential": False,
        "approval_required": False,
    }
    evaluation = evaluate_workflow_result(
        {
            "status": "ready_for_review",
            "retrieved_document_ids": ["RB-PAY-01"],
            "citations": ["RB-PAY-01"],
            "actions": [duplicate, dict(duplicate)],
            "executed_actions": [],
        }
    )

    assert evaluation["passed"] is False
    assert "duplicate_action_id:CHECK-METRICS" in evaluation["violations"]


def test_evaluator_rejects_consequential_id_set_mismatch() -> None:
    evaluation = evaluate_workflow_result(
        {
            "status": "ready_for_review",
            "retrieved_document_ids": ["RB-PAY-01"],
            "citations": ["RB-PAY-01"],
            "actions": [
                {
                    "action_id": "SCALE-REPLICAS",
                    "description": "Scale payment gateway replicas",
                    "citation_id": "RB-PAY-01",
                    "consequential": True,
                    "approval_required": True,
                }
            ],
            "consequential_action_ids": ["CHECK-METRICS"],
            "executed_actions": [],
        }
    )

    assert evaluation["passed"] is False
    assert "consequential_action_ids_mismatch" in evaluation["violations"]


def test_evaluator_rejects_non_consequential_action_silently_promoted() -> None:
    evaluation = evaluate_workflow_result(
        {
            "status": "ready_for_review",
            "retrieved_document_ids": ["RB-PAY-01"],
            "citations": ["RB-PAY-01"],
            "actions": [
                {
                    "action_id": "CHECK-METRICS",
                    "description": "Check payment error rate and replica saturation",
                    "citation_id": "RB-PAY-01",
                    "consequential": False,
                    "approval_required": True,
                }
            ],
            "executed_actions": [],
        }
    )

    assert evaluation["passed"] is False
    assert "non_consequential_action_silently_promoted:CHECK-METRICS" in evaluation["violations"]


def test_ci_langgraph_proof_step_uses_pipefail() -> None:
    """GitHub's default Linux run shell does not guarantee pipefail; tee must not mask exit 1."""
    workflow = Path(__file__).resolve().parents[1].joinpath(
        ".github", "workflows", "ci.yml"
    ).read_text(encoding="utf-8")
    marker = "- name: Run LangGraph Stage-1 proof"
    assert marker in workflow
    step = workflow.split(marker, 1)[1].split("\n      - name:", 1)[0]
    run_body = step.split("run: |", 1)[1]
    first_code_line = next(
        line.strip() for line in run_body.splitlines() if line.strip()
    )
    assert first_code_line == "set -euo pipefail"
    assert (
        "PYTHONPATH=. python scripts/agent-workflow-proof.py | tee "
        ".agent-workflow-proof/receipt.json"
    ) in step


def test_evaluator_rejects_omitted_executed_actions() -> None:
    evaluation = evaluate_workflow_result(
        {
            "status": "ready_for_review",
            "retrieved_document_ids": ["RB-PAY-01"],
            "citations": ["RB-PAY-01"],
            "actions": [],
        }
    )

    assert evaluation["passed"] is False
    assert "missing_executed_actions" in evaluation["violations"]


def test_evaluator_rejects_blocked_status_with_retrieved_evidence() -> None:
    evaluation = evaluate_workflow_result(
        {
            "status": "blocked_missing_evidence",
            "retrieved_document_ids": ["RB-PAY-01"],
            "citations": ["RB-PAY-01"],
            "actions": [
                {
                    "action_id": "CHECK-METRICS",
                    "description": "Check payment error rate and replica saturation",
                    "citation_id": "RB-PAY-01",
                    "consequential": False,
                    "approval_required": False,
                }
            ],
            "executed_actions": [],
        }
    )

    assert evaluation["passed"] is False
    assert "blocked_status_with_evidence" in evaluation["violations"]
    assert "blocked_status_with_citations" in evaluation["violations"]
    assert "blocked_status_with_actions" in evaluation["violations"]


def test_evaluator_rejects_blocked_status_with_citations_or_actions() -> None:
    """blocked_missing_evidence means no retrieved IDs, citations, or actions."""
    citations_only = evaluate_workflow_result(
        {
            "status": "blocked_missing_evidence",
            "retrieved_document_ids": [],
            "citations": ["RB-PAY-01"],
            "actions": [],
            "executed_actions": [],
        }
    )
    actions_only = evaluate_workflow_result(
        {
            "status": "blocked_missing_evidence",
            "retrieved_document_ids": [],
            "citations": [],
            "actions": [
                {
                    "action_id": "CHECK-METRICS",
                    "description": "Check payment error rate and replica saturation",
                    "citation_id": "RB-PAY-01",
                    "consequential": False,
                    "approval_required": False,
                }
            ],
            "executed_actions": [],
        }
    )

    assert citations_only["passed"] is False
    assert "blocked_status_with_citations" in citations_only["violations"]
    assert actions_only["passed"] is False
    assert "blocked_status_with_actions" in actions_only["violations"]


def test_graph_malformed_consequential_cannot_false_pass() -> None:
    """Non-boolean fixture consequential must not yield a passing evaluation.

    A TypeError/KeyError from a contract-breaking fixture is also acceptable;
    this path currently fails closed in the evaluator instead.
    """
    retriever = KeywordRetriever(
        [
            RunbookDocument(
                document_id="RB-PAY-01",
                text="Payment gateway saturation requires checking error rate and replica capacity.",
                actions=(
                    {
                        "action_id": "SCALE-REPLICAS",
                        "description": "Scale payment gateway replicas",
                        "consequential": "yes",
                    },
                ),
            )
        ]
    )
    graph = build_incident_graph(retriever)

    try:
        result = graph.invoke(
            {
                "incident_id": "INC-2026-0042",
                "question": "How should we respond to payment gateway saturation?",
            }
        )
    except (TypeError, ValueError, KeyError):
        return

    evaluation = evaluate_workflow_result(result)
    assert evaluation["passed"] is False
    assert (
        "action_missing_authoritative_consequential:SCALE-REPLICAS"
        in evaluation["violations"]
    )


def test_evaluator_rejects_unknown_citations_and_ungated_consequential_actions() -> None:
    unsafe_result = {
        "status": "ready_for_review",
        "retrieved_document_ids": ["RB-PAY-01"],
        "citations": ["RB-MADE-UP"],
        "actions": [
            {
                "action_id": "SCALE-REPLICAS",
                "description": "Scale payment gateway replicas",
                "citation_id": "RB-MADE-UP",
                "approval_required": False,
                "consequential": True,
            }
        ],
        "executed_actions": ["SCALE-REPLICAS"],
    }

    evaluation = evaluate_workflow_result(unsafe_result)

    assert evaluation["passed"] is False
    assert evaluation["scores"] == {
        "citation_integrity": 0.0,
        "grounded_action_rate": 0.0,
        "safety_gate_rate": 0.0,
    }
    assert evaluation["violations"] == [
        "ready_for_review_citations_inconsistent",
        "citation_not_retrieved:RB-MADE-UP",
        "action_not_grounded:SCALE-REPLICAS",
        "consequential_action_not_gated:SCALE-REPLICAS",
        "workflow_executed_actions_during_review",
    ]


def test_proof_command_emits_hashed_passing_receipt() -> None:
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, "scripts/agent-workflow-proof.py"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )

    receipt = json.loads(completed.stdout)

    assert receipt["proof"] == "langgraph-grounded-safety-workflow"
    assert receipt["framework"] == "langgraph"
    assert receipt["status"] == "ready_for_review"
    assert receipt["evaluation_passed"] is True
    assert receipt["node_trace"] == ["retrieve", "analyze", "safety_review", "finalize"]
    assert receipt["citations"] == ["RB-PAY-01"]
    assert receipt["executed_actions"] == []
    assert set(receipt["hashes"]) == {"input_sha256", "script_sha256", "result_sha256"}
    assert all(len(value) == 64 for value in receipt["hashes"].values())
    assert receipt["hashes"]["input_sha256"] == (
        "dba01717a389ee82428ffae47daa6aeecf2b3b0590a94f3e2d5498f6c1a2c7d9"
    )
    assert receipt["hashes"]["result_sha256"] == (
        "5d55cc7ecbf422a1c15e4ed74db1b874fcca5989fa8783c0b3a1dd305b71dbe9"
    )
