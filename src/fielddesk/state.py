"""LangGraph state for one triage run."""

from __future__ import annotations

import operator
from typing import Annotated, Any, Protocol, TypedDict

from fielddesk.schemas import ClassifierOutput, EscalationReason, NodeMetrics, TicketInput


class TriageState(TypedDict, total=False):
    run_id: str
    ticket: TicketInput
    missing_info: list[str]
    safety_terms: list[str]
    injection_markers: list[str]
    classification: ClassifierOutput
    draft: str
    guard_violations: list[str]
    escalation_reason: EscalationReason
    escalation_detail: str
    error: str
    metrics: Annotated[list[NodeMetrics], operator.add]


class NodeFn(Protocol):
    """Shape LangGraph's node protocol expects (the parameter must be named ``state``)."""

    async def __call__(self, state: TriageState) -> dict[str, Any]: ...
