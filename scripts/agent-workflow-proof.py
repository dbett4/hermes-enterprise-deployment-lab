#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_workflow.evaluation import evaluate_workflow_result
from agent_workflow.graph import build_incident_graph
from agent_workflow.retrieval import KeywordRetriever, RunbookDocument


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def main() -> int:
    fixture = {
        "incident_id": "INC-2026-0042",
        "question": "How should we respond to payment gateway saturation?",
        "documents": [
            {
                "document_id": "RB-PAY-01",
                "text": "Payment gateway saturation requires checking error rate and replica capacity.",
                "actions": [
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
                ],
            }
        ],
    }
    documents = [
        RunbookDocument(
            document_id=document["document_id"],
            text=document["text"],
            actions=tuple(document["actions"]),
        )
        for document in fixture["documents"]
    ]
    graph = build_incident_graph(KeywordRetriever(documents))
    result = graph.invoke(
        {
            "incident_id": fixture["incident_id"],
            "question": fixture["question"],
        }
    )
    evaluation = evaluate_workflow_result(result)
    result_summary = {
        "status": result["status"],
        "citations": result["citations"],
        "actions": result["actions"],
        "executed_actions": result["executed_actions"],
        "node_trace": result["node_trace"],
        "evaluation": evaluation,
    }
    receipt = {
        "proof": "langgraph-grounded-safety-workflow",
        "framework": "langgraph",
        "framework_version": importlib.metadata.version("langgraph"),
        "status": result["status"],
        "evaluation_passed": evaluation["passed"],
        "node_trace": result["node_trace"],
        "citations": result["citations"],
        "executed_actions": result["executed_actions"],
        "hashes": {
            "input_sha256": _sha256(_canonical_bytes(fixture)),
            "script_sha256": _sha256(Path(__file__).read_bytes()),
            "result_sha256": _sha256(_canonical_bytes(result_summary)),
        },
        "claim_boundary": (
            "Deterministic synthetic LangGraph proof; no model call, customer data, "
            "or production action execution."
        ),
    }
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if evaluation["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
