"""draft_reply: a stronger model writes a short reply grounded only in the ticket."""

from __future__ import annotations

from typing import Any

from fielddesk.config import Settings
from fielddesk.llm import CallSpec, LLMClient, log_event
from fielddesk.nodes import ticket_block
from fielddesk.prompts import load_prompt
from fielddesk.state import NodeFn, TriageState

_MISSING_LABELS = {
    "machine_hours": "machine hours",
    "warranty_status": "warranty status / delivery date",
    "error_code": "the exact error code shown on the display",
    "error_code_unrecognized_format": "the exact error code (the one given is not a known format)",
}


def make_draft_reply(llm: LLMClient, settings: Settings) -> NodeFn:
    system = load_prompt(settings.draft_prompt_version)

    async def draft_reply(state: TriageState) -> dict[str, Any]:
        c = state["classification"]
        missing = [_MISSING_LABELS[m] for m in state.get("missing_info", [])]
        user = (
            f"{ticket_block(state['ticket'])}\n\n"
            f"Triage: category={c.category.value}, priority={c.priority.value}.\n"
            f"Fields the dealer left empty: {', '.join(missing) if missing else 'none'}.\n\n"
            "Write the draft reply."
        )
        spec = CallSpec(
            node="draft_reply",
            model=settings.draft_model,
            system=system,
            user=user,
            max_tokens=settings.draft_max_tokens,
            thinking="between_tools",
            effort="medium",
        )
        text, metrics = await llm.text(spec)
        log_event(
            "node",
            run_id=state["run_id"],
            node="draft_reply",
            chars=len(text),
            latency_ms=metrics.latency_ms,
        )
        return {"draft": text, "metrics": [metrics]}

    return draft_reply
