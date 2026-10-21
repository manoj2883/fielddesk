"""The triage workflow.

    START -> validate -> classify -> (route) -> draft_reply -> check_draft -> END
                                        \\                          \\
                                         `-> escalate -> END        `-> escalate -> END

Fault tolerance (LangGraph >= 1.2):
- every node is async and gets a run timeout;
- LLM nodes retry transient API errors only (``llm.is_transient``);
- any node failure that survives its retries is routed by ``on_node_error`` to ``escalate`` with
  reason ``llm_error``. ``TriageAgent.run`` adds a last-resort catch, so callers always get a valid
  ``TriageResult``.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID, uuid4

from langgraph.errors import NodeError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, RetryPolicy, TimeoutPolicy

from fielddesk.config import Settings
from fielddesk.llm import LLMClient, is_transient, log_event
from fielddesk.nodes.check_draft import after_check, check_draft
from fielddesk.nodes.classify import make_classify
from fielddesk.nodes.draft_reply import make_draft_reply
from fielddesk.nodes.escalate import make_escalate
from fielddesk.nodes.route import Route, decide
from fielddesk.nodes.validate import validate
from fielddesk.schemas import EscalationReason, TicketInput, TriageResult
from fielddesk.state import TriageState


async def on_node_error(state: TriageState, error: NodeError) -> Command[str]:
    """Runs after a node's retries are exhausted. Never raises.

    Must be async: LangGraph runs the handler as a node, so the default timeout applies to it and
    a sync handler is rejected at compile time (JOURNAL, S3)."""
    exc = error.error
    detail = f"{error.node} failed: {type(exc).__name__}"
    log_event("node_error", run_id=state.get("run_id"), node=error.node, error=type(exc).__name__)
    return Command(
        update={
            "escalation_reason": EscalationReason.LLM_ERROR,
            "escalation_detail": detail,
            "error": f"{type(exc).__name__}: {str(exc)[:300]}",
        },
        goto="escalate",
    )


def build_graph(llm: LLMClient, settings: Settings) -> CompiledStateGraph[Any, Any, Any, Any]:
    threshold = settings.confidence_threshold

    def route(state: TriageState) -> Route:
        decision, _, _ = decide(state["classification"], state.get("safety_terms", []), threshold)
        return decision

    llm_retry = RetryPolicy(
        max_attempts=settings.retry_max_attempts,
        initial_interval=settings.retry_initial_interval_s,
        retry_on=is_transient,
    )
    no_retry = RetryPolicy(max_attempts=1)

    builder = (
        StateGraph(TriageState)
        .set_node_defaults(
            retry_policy=no_retry,
            timeout=TimeoutPolicy(run_timeout=settings.node_run_timeout_s),
            # Two-argument handlers are documented; the type stubs only model one argument.
            error_handler=on_node_error,  # type: ignore[arg-type]
        )
        .add_node("validate", validate)
        .add_node("classify", make_classify(llm, settings), retry_policy=llm_retry)
        .add_node("draft_reply", make_draft_reply(llm, settings), retry_policy=llm_retry)
        .add_node("check_draft", check_draft)
        .add_node("escalate", make_escalate(threshold))
        .add_edge(START, "validate")
        .add_edge("validate", "classify")
        .add_conditional_edges("classify", route, ["draft_reply", "escalate"])
        .add_edge("draft_reply", "check_draft")
        .add_conditional_edges("check_draft", after_check, {"escalate": "escalate", "done": END})
        .add_edge("escalate", END)
    )
    return builder.compile()


class TriageAgent:
    def __init__(self, settings: Settings, llm: LLMClient | None = None) -> None:
        self.settings = settings
        self.llm = llm or LLMClient(settings)
        self.graph = build_graph(self.llm, settings)

    def _meta(self) -> dict[str, dict[str, str]]:
        s = self.settings
        return {
            "prompt_versions": {
                "classify": s.classify_prompt_version,
                "draft": s.draft_prompt_version,
            },
            "model_ids": {"classify": s.classify_model, "draft": s.draft_model},
        }

    async def run(self, ticket: TicketInput, run_id: UUID | None = None) -> TriageResult:
        run_id = run_id or uuid4()
        try:
            state = cast(
                TriageState,
                await self.graph.ainvoke({"run_id": str(run_id), "ticket": ticket, "metrics": []}),
            )
            return to_result(state, run_id, self._meta())
        except Exception as exc:  # last resort: the contract is "always a valid result"
            log_event("run_error", run_id=str(run_id), error=type(exc).__name__)
            return TriageResult(
                run_id=run_id,
                ticket_id=ticket.ticket_id,
                escalate=True,
                escalation_reason=EscalationReason.LLM_ERROR,
                escalation_detail=f"graph failed: {type(exc).__name__}",
                error=f"{type(exc).__name__}: {str(exc)[:300]}",
                **self._meta(),
            )


def to_result(state: TriageState, run_id: UUID, meta: dict[str, dict[str, str]]) -> TriageResult:
    c = state.get("classification")
    reason = state.get("escalation_reason")
    metrics = state.get("metrics", [])
    return TriageResult(
        run_id=run_id,
        ticket_id=state["ticket"].ticket_id,
        predicted_category=c.category if c else None,
        predicted_priority=c.priority if c else None,
        confidence=c.confidence if c else None,
        safety_flag=bool((c and c.safety_flag) or state.get("safety_terms")),
        escalate=reason is not None,
        escalation_reason=reason,
        escalation_detail=state.get("escalation_detail"),
        draft_reply=None if reason is not None else state.get("draft"),
        node_metrics=metrics,
        total_cost_usd=round(sum(m.cost_usd for m in metrics), 8),
        error=state.get("error"),
        **meta,
    )
