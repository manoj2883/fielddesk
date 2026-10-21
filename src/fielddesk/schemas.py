"""Pydantic v2 models for every record FieldDesk reads or writes.

Persisted records carry ``schema_version`` so later chapters (judge, CI gate) can detect drift.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

SCHEMA_VERSION: Literal["1.0"] = "1.0"

# C0 controls except \t \n \r, plus DEL and C1 controls. Dealers paste from many tools.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def strip_control_chars(value: str) -> str:
    return _CONTROL_CHARS.sub("", value).strip()


class Category(StrEnum):
    PARTS_REQUEST = "parts_request"
    WARRANTY_CLAIM = "warranty_claim"
    TECHNICAL_FAULT = "technical_fault"
    SERVICE_SCHEDULING = "service_scheduling"
    BILLING = "billing"
    OTHER = "other"


class Priority(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class WarrantyStatus(StrEnum):
    IN_WARRANTY = "in_warranty"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class Difficulty(StrEnum):
    CLEAN = "clean"
    MESSY = "messy"
    TRICKY = "tricky"


class TrickyType(StrEnum):
    MULTI_ISSUE = "multi_issue"
    MISLEADING_WORDING = "misleading_wording"
    WARRANTY_IN_FAULT = "warranty_in_fault"
    PROMPT_INJECTION = "prompt_injection"
    WRONG_FIELD = "wrong_field"


class EscalationReason(StrEnum):
    LOW_CONFIDENCE = "low_confidence"
    CATEGORY_OTHER = "category_other"
    SAFETY = "safety"
    GUARD_FAILED = "guard_failed"
    LLM_ERROR = "llm_error"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


TicketId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]


class TicketInput(_Strict):
    """What dealer staff submit to the manufacturer."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    ticket_id: TicketId
    dealer_name: str = Field(min_length=1, max_length=120)
    machine_model: str = Field(min_length=1, max_length=60)
    subject: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=4000)
    error_code: str | None = Field(default=None, max_length=20)
    warranty_status: WarrantyStatus = WarrantyStatus.UNKNOWN
    machine_hours: int | None = Field(default=None, ge=0, le=100_000)

    @field_validator("dealer_name", "machine_model", "subject", "message", mode="before")
    @classmethod
    def _clean_text(cls, value: object) -> object:
        return strip_control_chars(value) if isinstance(value, str) else value

    @field_validator("error_code", mode="before")
    @classmethod
    def _clean_code(cls, value: object) -> object:
        if isinstance(value, str):
            cleaned = strip_control_chars(value)
            return cleaned or None
        return value


class TicketLabel(_Strict):
    """Ground truth. Derived from the scenario spec by ``fielddesk.labeling``, never by an LLM."""

    category: Category
    priority: Priority
    needs_human: bool
    safety_risk: bool
    difficulty: Difficulty
    tricky_type: TrickyType | None = None

    @model_validator(mode="after")
    def _tricky_type_matches_difficulty(self) -> Self:
        if (self.difficulty is Difficulty.TRICKY) != (self.tricky_type is not None):
            raise ValueError("tricky_type must be set if and only if difficulty is 'tricky'")
        return self


class LabeledTicket(_Strict):
    """One dataset row: the ticket the agent sees plus the label it never sees."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    ticket: TicketInput
    label: TicketLabel
    spec_hash: str = Field(min_length=8, max_length=64)


class ClassifierOutput(_Strict):
    """Structured output requested from the classify model."""

    category: Category
    priority: Priority
    confidence: float = Field(ge=0.0, le=1.0)
    safety_flag: bool
    rationale: str = Field(max_length=400)


class NodeMetrics(_Strict):
    node: str
    model_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    latency_ms: float = 0.0
    cost_usd: float = 0.0


class TriageResult(_Strict):
    """What the agent returns for one run. Always valid, even when every LLM call failed."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run_id: UUID = Field(default_factory=uuid4)
    ticket_id: TicketId
    predicted_category: Category | None = None
    predicted_priority: Priority | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    safety_flag: bool = False
    escalate: bool
    escalation_reason: EscalationReason | None = None
    escalation_detail: str | None = Field(default=None, max_length=1000)
    draft_reply: str | None = None
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    model_ids: dict[str, str] = Field(default_factory=dict)
    node_metrics: list[NodeMetrics] = Field(default_factory=list)
    total_cost_usd: float = 0.0
    error: str | None = Field(default=None, max_length=1000)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _escalation_is_consistent(self) -> Self:
        if self.escalate != (self.escalation_reason is not None):
            raise ValueError("escalation_reason must be set if and only if escalate is true")
        if self.escalate and self.draft_reply is not None:
            raise ValueError("an escalated result must not carry a draft reply")
        return self
