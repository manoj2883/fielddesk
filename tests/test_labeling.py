"""The spec -> label mapping. Each case names the rule in data/LABELING_GUIDE.md it exercises."""

import pytest

from fielddesk.labeling import MachineImpact as MI
from fielddesk.labeling import ScenarioFacts, label_from_facts, primary_category
from fielddesk.labeling import WarrantyDecision as WD
from fielddesk.schemas import Category as C
from fielddesk.schemas import Difficulty, Priority, TrickyType


def facts(**kw: object) -> ScenarioFacts:
    base: dict[str, object] = {
        "asks": (C.TECHNICAL_FAULT,),
        "machine_impact": MI.NONE,
        "safety_code": False,
        "safety_symptom": False,
        "time_sensitive": False,
        "warranty_decision": WD.NONE,
        "conflicting_asks": False,
        "difficulty": Difficulty.CLEAN,
    }
    base.update(kw)
    return ScenarioFacts(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("rule", "f", "priority"),
    [
        ("P1 safety code", facts(safety_code=True), Priority.HIGH),
        (
            "P1 safety symptom",
            facts(safety_symptom=True, machine_impact=MI.DEGRADED),
            Priority.HIGH,
        ),
        ("P2 machine down", facts(machine_impact=MI.DOWN), Priority.HIGH),
        ("P3 degraded", facts(machine_impact=MI.DEGRADED), Priority.MEDIUM),
        ("P4 parts", facts(asks=(C.PARTS_REQUEST,), time_sensitive=True), Priority.MEDIUM),
        ("P4 warranty", facts(asks=(C.WARRANTY_CLAIM,), time_sensitive=True), Priority.MEDIUM),
        ("P4 not billing", facts(asks=(C.BILLING,), time_sensitive=True), Priority.LOW),
        ("P5 scheduling", facts(asks=(C.SERVICE_SCHEDULING,)), Priority.LOW),
        ("P5 parts, no deadline", facts(asks=(C.PARTS_REQUEST,)), Priority.LOW),
    ],
)
def test_priority_rules(rule: str, f: ScenarioFacts, priority: Priority) -> None:
    assert label_from_facts(f).priority is priority, rule


@pytest.mark.parametrize(
    ("rule", "f", "needs_human"),
    [
        ("H1 safety", facts(safety_code=True), True),
        (
            "H2 outside policy",
            facts(asks=(C.WARRANTY_CLAIM,), warranty_decision=WD.OUTSIDE_POLICY),
            True,
        ),
        ("H3 other", facts(asks=(C.OTHER,)), True),
        (
            "H4 conflicting asks",
            facts(
                asks=(C.WARRANTY_CLAIM, C.PARTS_REQUEST),
                conflicting_asks=True,
                difficulty=Difficulty.TRICKY,
                tricky_type=TrickyType.MULTI_ISSUE,
            ),
            True,
        ),
        (
            "within policy alone",
            facts(asks=(C.WARRANTY_CLAIM,), warranty_decision=WD.WITHIN_POLICY),
            False,
        ),
        ("machine down alone", facts(machine_impact=MI.DOWN), False),
        ("routine billing", facts(asks=(C.BILLING,)), False),
    ],
)
def test_needs_human_rules(rule: str, f: ScenarioFacts, needs_human: bool) -> None:
    assert label_from_facts(f).needs_human is needs_human, rule


def test_safety_risk_is_code_or_symptom() -> None:
    assert label_from_facts(facts(safety_code=True)).safety_risk
    assert label_from_facts(facts(safety_symptom=True)).safety_risk
    assert not label_from_facts(facts()).safety_risk


def test_category_is_first_ask() -> None:
    f = facts(
        asks=(C.TECHNICAL_FAULT, C.WARRANTY_CLAIM),
        difficulty=Difficulty.TRICKY,
        tricky_type=TrickyType.WARRANTY_IN_FAULT,
    )
    assert label_from_facts(f).category is C.TECHNICAL_FAULT


def test_precedence_breaks_ties() -> None:
    assert primary_category((C.BILLING, C.PARTS_REQUEST, C.WARRANTY_CLAIM)) is C.WARRANTY_CLAIM
    assert primary_category((C.SERVICE_SCHEDULING, C.TECHNICAL_FAULT)) is C.TECHNICAL_FAULT


def test_stated_blocking_ask_overrides_precedence() -> None:
    asks = (C.WARRANTY_CLAIM, C.TECHNICAL_FAULT)
    assert primary_category(asks, blocking=C.TECHNICAL_FAULT) is C.TECHNICAL_FAULT
    with pytest.raises(ValueError, match="blocking"):
        primary_category(asks, blocking=C.BILLING)
    with pytest.raises(ValueError, match="at least one"):
        primary_category(())


def test_labels_ignore_difficulty_and_tricky_type() -> None:
    plain = label_from_facts(facts(machine_impact=MI.DEGRADED))
    tricky = label_from_facts(
        facts(
            machine_impact=MI.DEGRADED,
            difficulty=Difficulty.TRICKY,
            tricky_type=TrickyType.PROMPT_INJECTION,
        )
    )
    assert plain.model_dump(exclude={"difficulty", "tricky_type"}) == tricky.model_dump(
        exclude={"difficulty", "tricky_type"}
    )
