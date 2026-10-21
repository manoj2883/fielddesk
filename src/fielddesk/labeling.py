"""Deterministic labeling rules. The prose version is ``data/LABELING_GUIDE.md``; keep them in sync.

A scenario spec states *facts* about a ticket (what the machine is doing, which asks it makes,
whether a warranty decision falls outside policy). Every label field is computed from those facts,
so a label can always be traced back to a rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from fielddesk.schemas import Category, Difficulty, Priority, TicketLabel, TrickyType


class MachineImpact(StrEnum):
    DOWN = "down"  # machine cannot work, or must not be operated
    DEGRADED = "degraded"  # works with reduced capability or intermittent faults
    NONE = "none"  # no effect on the machine's operation (questions, billing, planning)


class WarrantyDecision(StrEnum):
    NONE = "none"  # no warranty/goodwill decision is asked for
    WITHIN_POLICY = "within_policy"  # in warranty, covered component, documented failure
    OUTSIDE_POLICY = "outside_policy"  # expired, goodwill, disputed, or unclear coverage


# Tie-break order for multi-issue tickets when it is not obvious which ask blocks the dealer.
# Earlier entries win. Rationale in LABELING_GUIDE.md section 3.
CATEGORY_PRECEDENCE: tuple[Category, ...] = (
    Category.WARRANTY_CLAIM,
    Category.PARTS_REQUEST,
    Category.TECHNICAL_FAULT,
    Category.SERVICE_SCHEDULING,
    Category.BILLING,
    Category.OTHER,
)


@dataclass(frozen=True)
class ScenarioFacts:
    asks: tuple[Category, ...]  # first ask is the one that blocks the dealer's next action
    machine_impact: MachineImpact
    safety_code: bool  # the ticket's error code is marked safety-relevant in the domain fixture
    safety_symptom: bool  # the text describes a brake/steering/fire/hydraulic-under-load hazard
    time_sensitive: bool  # a stated deadline or a machine waiting on the outcome
    warranty_decision: WarrantyDecision
    conflicting_asks: bool  # multi-issue ticket whose asks pull in different directions
    difficulty: Difficulty
    tricky_type: TrickyType | None = None


def primary_category(asks: tuple[Category, ...], blocking: Category | None = None) -> Category:
    """The ask that blocks the dealer's next action; precedence breaks ties."""
    if not asks:
        raise ValueError("a ticket needs at least one ask")
    if blocking is not None:
        if blocking not in asks:
            raise ValueError("blocking ask must be one of the ticket's asks")
        return blocking
    return min(asks, key=CATEGORY_PRECEDENCE.index)


def is_safety_risk(facts: ScenarioFacts) -> bool:
    return facts.safety_code or facts.safety_symptom


def derive_priority(facts: ScenarioFacts, category: Category) -> Priority:
    if is_safety_risk(facts) or facts.machine_impact is MachineImpact.DOWN:
        return Priority.HIGH
    if facts.machine_impact is MachineImpact.DEGRADED:
        return Priority.MEDIUM
    if facts.time_sensitive and category in (Category.PARTS_REQUEST, Category.WARRANTY_CLAIM):
        return Priority.MEDIUM
    return Priority.LOW


def derive_needs_human(facts: ScenarioFacts, category: Category) -> bool:
    return (
        is_safety_risk(facts)
        or facts.warranty_decision is WarrantyDecision.OUTSIDE_POLICY
        or category is Category.OTHER
        or facts.conflicting_asks
    )


def label_from_facts(facts: ScenarioFacts) -> TicketLabel:
    """The single entry point the generator uses to label a spec row."""
    category = facts.asks[0]
    return TicketLabel(
        category=category,
        priority=derive_priority(facts, category),
        needs_human=derive_needs_human(facts, category),
        safety_risk=is_safety_risk(facts),
        difficulty=facts.difficulty,
        tricky_type=facts.tricky_type,
    )
