"""classify: a fast model returns category, priority, confidence and a safety flag."""

from __future__ import annotations

from typing import Any

from fielddesk.config import Settings
from fielddesk.llm import CallSpec, LLMClient, log_event
from fielddesk.nodes import ticket_block
from fielddesk.prompts import load_prompt
from fielddesk.schemas import ClassifierOutput
from fielddesk.state import NodeFn, TriageState


def make_classify(llm: LLMClient, settings: Settings) -> NodeFn:
    system = load_prompt(settings.classify_prompt_version)

    async def classify(state: TriageState) -> dict[str, Any]:
        spec = CallSpec(
            node="classify",
            model=settings.classify_model,
            system=system,
            user=f"Classify this ticket.\n\n{ticket_block(state['ticket'])}",
            max_tokens=settings.classify_max_tokens,
            thinking="disabled",
            effort="low",
        )
        result, metrics = await llm.parse(spec, ClassifierOutput)
        log_event(
            "node",
            run_id=state["run_id"],
            node="classify",
            category=result.category,
            priority=result.priority,
            confidence=result.confidence,
            safety_flag=result.safety_flag,
            latency_ms=metrics.latency_ms,
        )
        return {"classification": result, "metrics": [metrics]}

    return classify
