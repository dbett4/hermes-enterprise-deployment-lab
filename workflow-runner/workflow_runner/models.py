from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DependencyCall(BaseModel):
    name: str
    method: str
    path: str
    status_code: int | None = None
    elapsed_ms: float
    correlation_id: str | None = None
    error: str | None = None


class ProposedAction(BaseModel):
    order: int
    description: str
    approval_required: bool
    source: str


class WorkflowReceipt(BaseModel):
    correlation_id: str
    incident_id: str
    outcome: str
    approval_required: bool = True
    proposed_actions: list[ProposedAction] = Field(default_factory=list)
    dependency_calls: list[DependencyCall] = Field(default_factory=list)
    timing_ms: dict[str, float] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    error: dict[str, Any] | None = None
