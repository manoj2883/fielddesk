"""Rejects generated text that leaks the label, invents codes, or contains real-world identifiers."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from fielddesk.schemas import Category

_LABEL_WORDS = re.compile(
    r"\b(categor(y|ies|ised|ized)|priority|triage|escalat\w*|label(ed|led)?|"
    r"parts[\s_]request|warranty[\s_]claim|technical[\s_]fault|service[\s_]scheduling|"
    r"parts_request|warranty_claim|technical_fault|service_scheduling)\b",
    re.IGNORECASE,
)
_REAL_BRANDS = re.compile(
    r"\b(john\s+deere|deere|case\s+ih|new\s+holland|kubota|massey(\s+ferguson)?|fendt|claas|agco|"
    r"caterpillar|cat\s+\d|bobcat|jcb|komatsu|valtra|mahindra|kioti|krone|vermeer)\b",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE = re.compile(r"(\+?\d{1,2}[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b")
_URL = re.compile(r"https?://|www\.", re.IGNORECASE)
_RX_CODE = re.compile(r"\bRX-?E\d{3}\b", re.IGNORECASE)
_PART_NO = re.compile(r"\b(p/?n|part\s*(no|number|#))\s*[:#]?\s*[A-Z0-9-]{4,}", re.IGNORECASE)


@dataclass(frozen=True)
class LeakCheck:
    ok: bool
    reasons: tuple[str, ...]


def check_text(text: str, allowed_code: str | None, injection_text: str | None = None) -> LeakCheck:
    """``injection_text`` is excluded from the label-word check: injected sentences say 'priority'
    on purpose (that is the attack the agent must resist)."""
    scan = text.replace(injection_text, " ") if injection_text else text
    reasons: list[str] = []
    for m in _LABEL_WORDS.finditer(scan):
        reasons.append(f"label_word:{m.group(0).lower()}")
    if _REAL_BRANDS.search(text):
        reasons.append("real_brand")
    if _EMAIL.search(text) or _PHONE.search(text) or _URL.search(text):
        reasons.append("pii_or_url")
    for code in {c.upper().replace("RXE", "RX-E") for c in _RX_CODE.findall(text)}:
        if allowed_code is None or code != allowed_code:
            reasons.append(f"invented_code:{code}")
    if _PART_NO.search(text):
        reasons.append("invented_part_number")
    return LeakCheck(ok=not reasons, reasons=tuple(sorted(set(reasons))))


def warranty_word_giveaway(rows: Iterable[tuple[str, Category]]) -> dict[str, float]:
    """How much does the bare word 'warranty' give away the warranty_claim category?

    Returns precision and recall of the rule "text contains 'warranty' -> warranty_claim". A rule
    that is both precise and complete would let a keyword baseline solve the category for free.
    """
    has = [("warrant" in text.lower(), cat is Category.WARRANTY_CLAIM) for text, cat in rows]
    tp = sum(1 for h, w in has if h and w)
    fp = sum(1 for h, w in has if h and not w)
    fn = sum(1 for h, w in has if not h and w)
    return {
        "precision": tp / (tp + fp) if tp + fp else 0.0,
        "recall": tp / (tp + fn) if tp + fn else 0.0,
        "n_with_word": float(tp + fp),
    }
