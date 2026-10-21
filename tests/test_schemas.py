import pytest
from pydantic import ValidationError

from fielddesk.schemas import (
    ClassifierOutput,
    Difficulty,
    EscalationReason,
    TicketInput,
    TicketLabel,
    TriageResult,
    TrickyType,
)


def _ticket(**overrides: object) -> TicketInput:
    base: dict[str, object] = {
        "ticket_id": "T-001",
        "dealer_name": "Coldwater Flats Implement",
        "machine_model": "Ridgeline T680",
        "subject": "Shift fault",
        "message": "RX-E214 on startup, will not shift.",
    }
    base.update(overrides)
    return TicketInput.model_validate(base)


def test_control_characters_are_stripped() -> None:
    t = _ticket(message="no\x00 shift\x1b[31m\n second line\x7f", subject="  hi\x08 ")
    assert t.message == "no shift[31m\n second line"
    assert t.subject == "hi"


def test_empty_error_code_becomes_none() -> None:
    assert _ticket(error_code=" \x00 ").error_code is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("message", "x" * 4001),
        ("subject", ""),
        ("machine_hours", -1),
        ("ticket_id", "bad id with spaces"),
        ("warranty_status", "lifetime"),
    ],
)
def test_invalid_inputs_are_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        _ticket(**{field: value})


def test_extra_fields_are_forbidden() -> None:
    with pytest.raises(ValidationError):
        _ticket(priority="high")


def test_schema_version_is_present() -> None:
    assert _ticket().model_dump()["schema_version"] == "1.0"


def test_tricky_type_requires_tricky_difficulty() -> None:
    common = {"category": "billing", "priority": "low", "needs_human": False, "safety_risk": False}
    with pytest.raises(ValidationError):
        TicketLabel.model_validate({**common, "difficulty": "clean", "tricky_type": "wrong_field"})
    with pytest.raises(ValidationError):
        TicketLabel.model_validate({**common, "difficulty": "tricky"})
    label = TicketLabel.model_validate(
        {**common, "difficulty": "tricky", "tricky_type": "prompt_injection"}
    )
    assert label.difficulty is Difficulty.TRICKY
    assert label.tricky_type is TrickyType.PROMPT_INJECTION


def test_triage_result_escalation_invariants() -> None:
    ok = TriageResult(ticket_id="T-1", escalate=True, escalation_reason=EscalationReason.SAFETY)
    assert ok.draft_reply is None
    with pytest.raises(ValidationError):
        TriageResult(ticket_id="T-1", escalate=True)
    with pytest.raises(ValidationError):
        TriageResult(ticket_id="T-1", escalate=False, escalation_reason=EscalationReason.SAFETY)
    with pytest.raises(ValidationError):
        TriageResult(
            ticket_id="T-1",
            escalate=True,
            escalation_reason=EscalationReason.LLM_ERROR,
            draft_reply="Hello",
        )


def test_triage_result_round_trips_through_json() -> None:
    r = TriageResult(ticket_id="T-1", escalate=False, draft_reply="Thanks", confidence=0.9)
    assert TriageResult.model_validate_json(r.model_dump_json()) == r


def test_classifier_confidence_bounds() -> None:
    base = {"category": "billing", "priority": "low", "safety_flag": False, "rationale": "r"}
    with pytest.raises(ValidationError):
        ClassifierOutput.model_validate({**base, "confidence": 1.2})
