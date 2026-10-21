"""Deterministic checks. No LLM, no network, fully unit-tested.

These guards are tuned to over-fire. A false positive costs a human a few seconds of review. A
false negative is a missed safety issue or an invented commitment sent to a dealer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from fielddesk.schemas import TicketInput


def _any(*alternatives: str) -> str:
    return "|".join(alternatives)


# --- safety ------------------------------------------------------------------------------------

_SAFETY_PATTERNS: dict[str, str] = {
    "brakes": _any(
        r"\b(no|lost|losing|weak|soft|failed|failing)\s+brakes?\b",
        r"\bbrakes?\s+((are|is)\s+)?(fail\w*|gone|out|not\s+(working|holding)|won'?t\s+hold)",
        r"\bwon'?t\s+stop\b",
        r"\bpark(ing)?\s+brake\s+((is\s+)?not\s+(engag\w*|holding)|fail\w*)",
    ),
    "steering": _any(
        r"\bsteering\s+(lock\w*|lost|loss|failed|failure|went\s+out|gone|heavy|stiff)\b",
        r"\b(lost|no)\s+steering\b",
        r"\bcan'?t\s+steer\b",
    ),
    "fire": r"\b(fire|flames?|smoke|smoking|burning\s+smell|smell\w*\s+burning|melted|scorch\w*)\b",
    "hydraulic_under_load": _any(
        r"\b(boom|bucket|loader\s+arms?|load)\s+(dropp\w*|fell|falls|drift\w*\s+down|came\s+down)\b",
        r"\b(dropp\w*|fell)\s+(the\s+)?(load|bucket|boom)\b",
    ),
    "injury": _any(
        r"\b(injur\w*|hurt|hospital|ambulance|near\s+miss)\b",
        r"\b(rolled\s+over|rollover|tip(ped)?\s+over)\b",
    ),
}
_SAFETY_RE = {name: re.compile(p, re.IGNORECASE) for name, p in _SAFETY_PATTERNS.items()}


def detect_safety_terms(text: str) -> list[str]:
    """Names of the hazard groups the text mentions, in a stable order."""
    return [name for name, rx in _SAFETY_RE.items() if rx.search(text)]


# --- prompt injection --------------------------------------------------------------------------

_INJECTION_PATTERNS: dict[str, str] = {
    "ignore_instructions": r"\b(ignore|disregard|forget)\b.{0,30}\b(instructions?|rules|prompt)\b",
    "role_override": _any(
        r"\byou\s+are\s+now\b",
        r"\bact\s+as\b",
        r"\bpretend\s+to\s+be\b",
        r"\bnew\s+instructions?\b",
    ),
    "system_reference": _any(
        r"\bsystem\s+prompt\b", r"\b(ai|llm|language\s+model|assistant)\s*[,:]"
    ),
    "label_steering": r"\b(mark|set|classify|label|flag|categori[sz]e|route)\s+"
    r"(this|it|the\s+ticket)\b.{0,25}\b(priority|low|high|medium|urgent|as)\b",
    "delimiter_spoof": r"</?\s*(ticket|system|instructions?|message)\s*>",
}
_INJECTION_RE = {
    name: re.compile(p, re.IGNORECASE | re.DOTALL) for name, p in _INJECTION_PATTERNS.items()
}


def detect_injection(text: str) -> list[str]:
    return [name for name, rx in _INJECTION_RE.items() if rx.search(text)]


# --- draft grounding ---------------------------------------------------------------------------

# Part numbers and error codes: letter prefix, optional dash, digits (RX-E214, RP-44821, 87412-A).
_CODE_RE = re.compile(r"\b(?:[A-Z]{1,4}-?[A-Z]?\d{3,}[A-Z0-9-]*|\d{4,}-[A-Z0-9]+)\b")
_MONEY_RE = re.compile(
    r"(\$\s?\d[\d,]*(\.\d+)?|\b\d[\d,]*(\.\d+)?\s?(usd|dollars)\b)", re.IGNORECASE
)
_COMMITMENT_RE = re.compile(
    _any(
        r"\b(warranty|claim|goodwill)\s+(is\s+)?(approved|accepted|granted)\b",
        r"\b(is|are|will\s+be|has\s+been|have\s+been|was|were)\s+(approved|covered)\b",
        r"\b(we|ridgeline)\s+(will|can|shall)\s+(replace|reimburse|refund|cover|credit|pay)\b",
        r"\bfree\s+of\s+charge\b",
        r"\bat\s+no\s+(cost|charge)\b",
        r"\bguarantee\w*\b",
    ),
    re.IGNORECASE,
)
_WEEKDAY = r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
_TIMELINE_RE = re.compile(
    _any(
        r"\b(within|in)\s+\d+\s+(business\s+)?(hours?|days?|weeks?)\b",
        r"\bship(s|ped|ping)?\s+(today|tomorrow|overnight)\b",
        rf"\bby\s+({_WEEKDAY}|tomorrow|end\s+of\s+(day|week))\b",
    ),
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GuardResult:
    passed: bool
    violations: list[str] = field(default_factory=list)


def _ticket_text(ticket: TicketInput) -> str:
    # machine_model is included because model names such as "T680" look like codes to _CODE_RE.
    parts = [ticket.machine_model, ticket.subject, ticket.message, ticket.error_code or ""]
    return " ".join(filter(None, parts))


def check_draft(draft: str, ticket: TicketInput) -> GuardResult:
    """Fail if the draft states anything specific that the ticket did not give us."""
    source = _ticket_text(ticket)
    source_upper = source.upper()
    source_lower = source.lower()
    violations: list[str] = []

    for code in sorted(set(_CODE_RE.findall(draft))):
        if code.upper() not in source_upper:
            violations.append(f"ungrounded_code:{code}")
    for match in _MONEY_RE.finditer(draft):
        if match.group(0).lower() not in source_lower:
            violations.append(f"ungrounded_amount:{match.group(0)}")
    for match in _COMMITMENT_RE.finditer(draft):
        violations.append(f"commitment:{match.group(0).lower()}")
    for match in _TIMELINE_RE.finditer(draft):
        if match.group(0).lower() not in source_lower:
            violations.append(f"ungrounded_timeline:{match.group(0).lower()}")

    return GuardResult(passed=not violations, violations=violations)
