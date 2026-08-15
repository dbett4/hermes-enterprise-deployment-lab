from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from agent_workflow.evaluation import action_provenance_sha256
from agent_workflow.retrieval import KeywordRetriever, RunbookDocument


class IncidentGraphState(TypedDict, total=False):
    incident_id: str
    question: str
    retrieved_documents: list[RunbookDocument]
    retrieved_document_ids: list[str]
    retrieved_action_provenance: list[str]
    actions: list[dict[str, Any]]
    consequential_action_ids: list[str]
    citations: list[str]
    executed_actions: list[str]
    status: str
    node_trace: Annotated[list[str], operator.add]


def build_incident_graph(retriever: KeywordRetriever):
    """Compile a read-only workflow with retrieval, analysis, and safety review."""

    def retrieve(state: IncidentGraphState) -> IncidentGraphState:
        documents = retriever.search(state.get("question", ""))
        return {
            "retrieved_documents": documents,
            "retrieved_document_ids": [document.document_id for document in documents],
            "retrieved_action_provenance": [
                action_provenance_sha256(document.document_id, action)
                for document in documents
                for action in document.actions
            ],
            "node_trace": ["retrieve"],
        }

    def analyze(state: IncidentGraphState) -> IncidentGraphState:
        actions: list[dict[str, Any]] = []
        consequential_ids: list[str] = []
        for document in state.get("retrieved_documents", []):
            for source_action in document.actions:
                action_id = str(source_action["action_id"])
                consequential = source_action["consequential"]
                if consequential is True:
                    consequential_ids.append(action_id)
                actions.append(
                    {
                        "action_id": action_id,
                        "description": str(source_action["description"]),
                        "citation_id": document.document_id,
                        "consequential": consequential,
                        "approval_required": False,
                    }
                )
        return {
            "actions": actions,
            "consequential_action_ids": consequential_ids,
            "node_trace": ["analyze"],
        }

    def safety_review(state: IncidentGraphState) -> IncidentGraphState:
        reviewed_actions = [
            {
                **action,
                "approval_required": action.get("consequential") is True,
            }
            for action in state.get("actions", [])
        ]
        return {
            "actions": reviewed_actions,
            "executed_actions": [],
            "node_trace": ["safety_review"],
        }

    def finalize(state: IncidentGraphState) -> IncidentGraphState:
        documents = state.get("retrieved_documents", [])
        return {
            "status": "ready_for_review" if documents else "blocked_missing_evidence",
            "citations": [document.document_id for document in documents],
            "node_trace": ["finalize"],
        }

    graph = StateGraph(IncidentGraphState)
    graph.add_node("retrieve", retrieve)
    graph.add_node("analyze", analyze)
    graph.add_node("safety_review", safety_review)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "analyze")
    graph.add_edge("analyze", "safety_review")
    graph.add_edge("safety_review", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()
