"""Chapter 2 seam: an LLM judge that grades draft replies.

Chapter 1 ships only the interface so the baseline's per-ticket predictions file and the results
JSON already carry everything a judge needs (ticket, label, draft, run metadata).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from fielddesk.schemas import TicketInput, TriageResult


@dataclass(frozen=True)
class JudgeVerdict:
    score: float  # 0.0-1.0
    passed: bool
    rubric_scores: dict[str, float] = field(default_factory=dict)
    rationale: str = ""


class Judge(Protocol):
    name: str
    version: str

    async def grade(self, ticket: TicketInput, result: TriageResult) -> JudgeVerdict: ...


class NotImplementedJudge:
    name = "not_implemented"
    version = "0"

    async def grade(self, ticket: TicketInput, result: TriageResult) -> JudgeVerdict:
        raise NotImplementedError("The LLM judge arrives in Chapter 2.")
