"""Every escalation path through the pure route function."""

import pytest

from fielddesk.nodes.route import decide
from fielddesk.schemas import Category, ClassifierOutput, EscalationReason, Priority

T = 0.7


def c(
    category: Category = Category.PARTS_REQUEST, confidence: float = 0.9, safety: bool = False
) -> ClassifierOutput:
    return ClassifierOutput(
        category=category,
        priority=Priority.MEDIUM,
        confidence=confidence,
        safety_flag=safety,
        rationale="r",
    )


@pytest.mark.parametrize(
    ("name", "classification", "terms", "route", "reason"),
    [
        ("clean ticket drafts", c(), [], "draft_reply", None),
        ("exactly at threshold drafts", c(confidence=0.7), [], "draft_reply", None),
        ("below threshold", c(confidence=0.69), [], "escalate", EscalationReason.LOW_CONFIDENCE),
        (
            "category other",
            c(category=Category.OTHER),
            [],
            "escalate",
            EscalationReason.CATEGORY_OTHER,
        ),
        ("llm safety flag only", c(safety=True), [], "escalate", EscalationReason.SAFETY),
        ("guard terms only", c(), ["brakes"], "escalate", EscalationReason.SAFETY),
        ("both safety sources", c(safety=True), ["fire"], "escalate", EscalationReason.SAFETY),
        (
            "safety outranks other + low confidence",
            c(category=Category.OTHER, confidence=0.2),
            ["steering"],
            "escalate",
            EscalationReason.SAFETY,
        ),
        (
            "other outranks low confidence",
            c(category=Category.OTHER, confidence=0.2),
            [],
            "escalate",
            EscalationReason.CATEGORY_OTHER,
        ),
    ],
)
def test_route_table(
    name: str,
    classification: ClassifierOutput,
    terms: list[str],
    route: str,
    reason: EscalationReason | None,
) -> None:
    got_route, got_reason, detail = decide(classification, terms, T)
    assert (got_route, got_reason) == (route, reason), name
    assert (detail is None) == (reason is None)


def test_safety_detail_names_both_sources() -> None:
    _, _, detail = decide(c(safety=True), ["fire", "injury"], T)
    assert detail == "classifier safety_flag; guard terms: fire, injury"


def test_threshold_is_a_parameter() -> None:
    assert decide(c(confidence=0.85), [], 0.9)[0] == "escalate"
    assert decide(c(confidence=0.85), [], 0.8)[0] == "draft_reply"
