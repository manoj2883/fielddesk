"""Full graph with a scripted fake LLM: happy path, every escalation path, and failure handling."""

import asyncio
from pathlib import Path
from typing import Any, cast

import anthropic
import pytest

from fielddesk.config import Settings
from fielddesk.graph import TriageAgent
from fielddesk.llm import LLMClient
from fielddesk.schemas import (
    Category,
    ClassifierOutput,
    EscalationReason,
    Priority,
    TicketInput,
    TriageResult,
)
from tests.fakes import FakeAnthropic, api_status_error, message

TICKET = TicketInput(
    ticket_id="T-42",
    dealer_name="Coldwater Flats Implement",
    machine_model="Ridgeline T680",
    subject="RX-E214 shift fault",
    message="Tractor throws RX-E214 on startup and will not shift out of neutral.",
    error_code="RX-E214",
    warranty_status="in_warranty",
    machine_hours=1200,
)

GOOD_CLASS = ClassifierOutput(
    category=Category.TECHNICAL_FAULT,
    priority=Priority.HIGH,
    confidence=0.92,
    safety_flag=False,
    rationale="r",
)
GOOD_DRAFT = (
    "Thanks for the RX-E214 report on the Ridgeline T680. Could you send a photo of the connector?"
)


def agent(
    tmp_path: Path, classify: Any = GOOD_CLASS, draft: Any = GOOD_DRAFT, **settings: Any
) -> tuple[TriageAgent, FakeAnthropic]:
    """classify/draft: a value, an exception, or a callable taking the call index."""
    counters = {"classify": 0, "draft": 0}

    def respond(kw: dict[str, Any]) -> Any:
        node = "classify" if "output_format" in kw else "draft"
        counters[node] += 1
        script = classify if node == "classify" else draft
        if callable(script) and not isinstance(script, type):
            script = script(counters[node])
        if isinstance(script, BaseException | asyncio.Future):
            return script
        if node == "classify":
            return message(parsed=script)
        return message(text=script)

    s = Settings(
        anthropic_api_key=None,
        ledger_path=tmp_path / "ledger.jsonl",
        retry_initial_interval_s=0.01,
        classify_model="claude-haiku-5-5",
        draft_model="claude-sonnet-5-5",
        **settings,
    )
    fake = FakeAnthropic(respond)
    return TriageAgent(s, LLMClient(s, client=cast(anthropic.AsyncAnthropic, fake))), fake


def assert_valid_escalation(r: TriageResult, reason: EscalationReason) -> None:
    assert r.escalate
    assert r.escalation_reason is reason
    assert r.draft_reply is None
    TriageResult.model_validate_json(r.model_dump_json())


async def test_happy_path_drafts(tmp_path: Path) -> None:
    a, fake = agent(tmp_path)
    r = await a.run(TICKET)
    assert not r.escalate
    assert r.draft_reply == GOOD_DRAFT
    assert r.predicted_category is Category.TECHNICAL_FAULT
    assert [m.node for m in r.node_metrics] == [
        "validate",
        "classify",
        "draft_reply",
        "check_draft",
    ]
    assert r.total_cost_usd > 0
    assert r.model_ids == {"classify": "claude-haiku-5-5", "draft": "claude-sonnet-5-5"}
    assert r.prompt_versions == {"classify": "classify_v1", "draft": "draft_v1"}
    assert "<ticket>" in fake.messages.calls[0]["messages"][0]["content"]


@pytest.mark.parametrize(
    ("classification", "reason"),
    [
        (GOOD_CLASS.model_copy(update={"confidence": 0.4}), EscalationReason.LOW_CONFIDENCE),
        (
            GOOD_CLASS.model_copy(update={"category": Category.OTHER}),
            EscalationReason.CATEGORY_OTHER,
        ),
        (GOOD_CLASS.model_copy(update={"safety_flag": True}), EscalationReason.SAFETY),
    ],
)
async def test_route_escalations_skip_the_draft(
    tmp_path: Path, classification: ClassifierOutput, reason: EscalationReason
) -> None:
    a, fake = agent(tmp_path, classify=classification)
    r = await a.run(TICKET)
    assert_valid_escalation(r, reason)
    assert len(fake.messages.calls) == 1  # no draft call was made


async def test_deterministic_safety_guard_overrides_the_llm(tmp_path: Path) -> None:
    a, _ = agent(tmp_path)  # classifier says no safety issue
    r = await a.run(
        TICKET.model_copy(update={"message": "Smoke from the exhaust, operator shut it down."})
    )
    assert_valid_escalation(r, EscalationReason.SAFETY)
    assert r.safety_flag
    assert r.escalation_detail == "guard terms: fire"


async def test_ungrounded_draft_escalates(tmp_path: Path) -> None:
    a, _ = agent(
        tmp_path, draft="Good news, the claim is approved and part RP-44821 ships tomorrow."
    )
    r = await a.run(TICKET)
    assert_valid_escalation(r, EscalationReason.GUARD_FAILED)
    assert "ungrounded_code:RP-44821" in (r.escalation_detail or "")
    assert r.predicted_category is Category.TECHNICAL_FAULT  # classification survives


async def test_transient_error_is_retried_then_succeeds(tmp_path: Path) -> None:
    a, fake = agent(tmp_path, classify=lambda n: api_status_error(529) if n == 1 else GOOD_CLASS)
    r = await a.run(TICKET)
    assert not r.escalate
    assert len(fake.messages.calls) == 3  # classify x2, draft x1


async def test_persistent_529_ends_in_llm_error(tmp_path: Path) -> None:
    a, fake = agent(tmp_path, classify=api_status_error(529))
    r = await a.run(TICKET)
    assert_valid_escalation(r, EscalationReason.LLM_ERROR)
    assert len(fake.messages.calls) == 3  # retry_max_attempts
    assert r.escalation_detail == "classify failed: OverloadedError"
    assert r.predicted_category is None


async def test_bad_request_is_not_retried(tmp_path: Path) -> None:
    a, fake = agent(tmp_path, classify=api_status_error(400))
    r = await a.run(TICKET)
    assert_valid_escalation(r, EscalationReason.LLM_ERROR)
    assert len(fake.messages.calls) == 1


async def test_refusal_is_not_retried(tmp_path: Path) -> None:
    a, fake = agent(tmp_path)
    fake.messages.responder = lambda kw: message(stop_reason="refusal")
    r = await a.run(TICKET)
    assert_valid_escalation(r, EscalationReason.LLM_ERROR)
    assert "LLMResponseError" in (r.error or "")
    assert len(fake.messages.calls) == 1


async def test_draft_timeout_ends_in_llm_error(tmp_path: Path) -> None:
    a, fake = agent(tmp_path, node_run_timeout_s=0.2, retry_max_attempts=2)

    async def slow_create(**kw: Any) -> Any:
        fake.messages.calls.append(kw)
        await asyncio.sleep(5)

    fake.messages.create = slow_create  # type: ignore[method-assign]
    r = await a.run(TICKET)
    assert_valid_escalation(r, EscalationReason.LLM_ERROR)
    assert r.escalation_detail == "draft_reply failed: NodeTimeoutError"
    draft_calls = [c for c in fake.messages.calls if "output_format" not in c]
    assert len(draft_calls) == 2  # the timeout was retried once
    assert r.predicted_category is Category.TECHNICAL_FAULT


async def test_budget_exhaustion_ends_in_llm_error(tmp_path: Path) -> None:
    (tmp_path / "ledger.jsonl").write_text('{"cost_usd": 4.99999}\n')
    a, fake = agent(tmp_path)
    r = await a.run(TICKET)
    assert_valid_escalation(r, EscalationReason.LLM_ERROR)
    assert "BudgetExceededError" in (r.error or "")
    assert fake.messages.calls == []


async def test_unexpected_crash_still_returns_a_result(tmp_path: Path) -> None:
    a, _ = agent(tmp_path)

    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("graph exploded")

    a.graph.ainvoke = boom  # type: ignore[method-assign]
    r = await a.run(TICKET)
    assert_valid_escalation(r, EscalationReason.LLM_ERROR)
    assert r.escalation_detail == "graph failed: RuntimeError"


async def test_each_run_gets_a_new_run_id(tmp_path: Path) -> None:
    a, _ = agent(tmp_path)
    r1, r2 = await a.run(TICKET), await a.run(TICKET)
    assert r1.run_id != r2.run_id
