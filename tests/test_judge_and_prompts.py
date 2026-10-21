import pytest

from fielddesk.judge import Judge, NotImplementedJudge
from fielddesk.prompts import load_prompt
from fielddesk.schemas import EscalationReason, TicketInput, TriageResult


async def test_judge_stub_satisfies_protocol_and_refuses() -> None:
    judge: Judge = NotImplementedJudge()
    ticket = TicketInput(
        ticket_id="T", dealer_name="d", machine_model="m", subject="s", message="m"
    )
    result = TriageResult(ticket_id="T", escalate=True, escalation_reason=EscalationReason.SAFETY)
    with pytest.raises(NotImplementedError, match="Chapter 2"):
        await judge.grade(ticket, result)


def test_prompts_load_and_unknown_version_fails() -> None:
    assert "<ticket>" in load_prompt("classify_v1")
    assert "Ridgeline Dealer Support" in load_prompt("draft_v1")
    with pytest.raises(FileNotFoundError):
        load_prompt("classify_v999")
