"""check_draft: no LLM. Fails the draft if it states anything the ticket did not give us."""

from __future__ import annotations

import time
from typing import Any

from fielddesk.guards import check_draft as run_guard
from fielddesk.llm import log_event
from fielddesk.schemas import EscalationReason, NodeMetrics
from fielddesk.state import TriageState


async def check_draft(state: TriageState) -> dict[str, Any]:
    started = time.perf_counter()
    result = run_guard(state["draft"], state["ticket"])
    latency = round((time.perf_counter() - started) * 1000, 2)
    log_event(
        "node",
        run_id=state["run_id"],
        node="check_draft",
        passed=result.passed,
        violations=result.violations,
        latency_ms=latency,
    )
    update: dict[str, Any] = {
        "guard_violations": result.violations,
        "metrics": [NodeMetrics(node="check_draft", latency_ms=latency)],
    }
    if not result.passed:
        update["escalation_reason"] = EscalationReason.GUARD_FAILED
        update["escalation_detail"] = "; ".join(result.violations)[:1000]
    return update


def after_check(state: TriageState) -> str:
    return "escalate" if state.get("escalation_reason") else "done"
