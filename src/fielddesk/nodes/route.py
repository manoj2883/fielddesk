"""route: a pure function. Decides whether a ticket gets a draft or goes straight to a human."""

from __future__ import annotations

from typing import Literal

from fielddesk.schemas import Category, ClassifierOutput, EscalationReason

Route = Literal["draft_reply", "escalate"]


def decide(
    classification: ClassifierOutput,
    safety_terms: list[str],
    threshold: float,
) -> tuple[Route, EscalationReason | None, str | None]:
    """Escalate on safety (LLM flag OR deterministic guard), then category ``other``, then low
    confidence. Safety is checked first so its reason is the one recorded when several apply."""
    if classification.safety_flag or safety_terms:
        sources = []
        if classification.safety_flag:
            sources.append("classifier safety_flag")
        if safety_terms:
            sources.append("guard terms: " + ", ".join(safety_terms))
        return "escalate", EscalationReason.SAFETY, "; ".join(sources)
    if classification.category is Category.OTHER:
        return "escalate", EscalationReason.CATEGORY_OTHER, "category is 'other'"
    if classification.confidence < threshold:
        detail = f"confidence {classification.confidence:.2f} < threshold {threshold:.2f}"
        return "escalate", EscalationReason.LOW_CONFIDENCE, detail
    return "draft_reply", None, None
