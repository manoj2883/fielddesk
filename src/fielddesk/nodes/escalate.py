"""escalate: records why a human must handle the ticket. Never produces a draft."""

from __future__ import annotations

from typing import Any

from fielddesk.llm import log_event
from fielddesk.nodes.route import decide
from fielddesk.schemas import EscalationReason, NodeMetrics
from fielddesk.state import NodeFn, TriageState


def make_escalate(threshold: float) -> NodeFn:
    async def escalate(state: TriageState) -> dict[str, Any]:
        reason = state.get("escalation_reason")
        detail = state.get("escalation_detail")
        if reason is None and "classification" in state:
            # Reached from the route edge: recompute with the same pure function the edge used.
            _, reason, detail = decide(
                state["classification"], state.get("safety_terms", []), threshold
            )
        if reason is None:
            reason, detail = EscalationReason.LLM_ERROR, "escalated without a recorded reason"
        log_event("node", run_id=state["run_id"], node="escalate", reason=reason, detail=detail)
        return {
            "escalation_reason": reason,
            "escalation_detail": detail,
            "metrics": [NodeMetrics(node="escalate")],
        }

    return escalate
