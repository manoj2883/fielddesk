import json
from pathlib import Path
from typing import Any, cast

import anthropic
import pytest
from langgraph.errors import NodeTimeoutError

from fielddesk.config import Settings
from fielddesk.llm import (
    BudgetExceededError,
    CallSpec,
    LLMClient,
    LLMResponseError,
    MissingAPIKeyError,
    SpendLedger,
    is_transient,
)
from fielddesk.schemas import Category, ClassifierOutput, Priority
from tests.fakes import FakeAnthropic, api_status_error, message, usage


def make_client(
    tmp_path: Path, responder: Any, cap: float = 5.0
) -> tuple[LLMClient, FakeAnthropic]:
    settings = Settings(
        ledger_path=tmp_path / "ledger.jsonl", max_spend_usd=cap, anthropic_api_key=None
    )
    fake = FakeAnthropic(responder)
    return LLMClient(settings, client=cast(anthropic.AsyncAnthropic, fake)), fake


SPEC = CallSpec(
    node="classify", model="claude-haiku-5-5", system="sys " * 200, user="ticket", max_tokens=256
)
PARSED = ClassifierOutput(
    category=Category.BILLING,
    priority=Priority.LOW,
    confidence=0.9,
    safety_flag=False,
    rationale="r",
)


async def test_parse_returns_model_and_costed_metrics(tmp_path: Path) -> None:
    llm, fake = make_client(
        tmp_path,
        lambda kw: message(parsed=PARSED, u=usage(inp=100, out=50, cache_read=1000, cache_write=0)),
    )
    out, metrics = await llm.parse(SPEC, ClassifierOutput)
    assert out == PARSED
    # Haiku 5.5: 100*0.10 + 50*0.50 + 1000*0.01 per MTok
    assert metrics.cost_usd == pytest.approx((10 + 25 + 10) / 1_000_000)
    assert metrics.cache_read_tokens == 1000
    call = fake.messages.calls[0]
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert call["thinking"] == {"type": "disabled"}
    assert "temperature" not in call


async def test_effort_is_sent_in_output_config(tmp_path: Path) -> None:
    llm, fake = make_client(tmp_path, lambda kw: message(text="Hello dealer"))
    spec = CallSpec(
        node="draft",
        model="claude-sonnet-5-5",
        system="s",
        user="u",
        max_tokens=100,
        thinking="between_tools",
        effort="medium",
    )
    text, _ = await llm.text(spec)
    assert text == "Hello dealer"
    assert fake.messages.calls[0]["output_config"] == {"effort": "medium"}
    assert fake.messages.calls[0]["thinking"] == {"type": "between_tools"}


async def test_spend_is_persisted_and_reloaded(tmp_path: Path) -> None:
    llm, _ = make_client(tmp_path, lambda kw: message(parsed=PARSED, u=usage(inp=1_000_000, out=0)))
    await llm.parse(SPEC, ClassifierOutput)
    lines = (tmp_path / "ledger.jsonl").read_text().splitlines()
    assert json.loads(lines[0])["cost_usd"] == pytest.approx(0.10)
    assert SpendLedger(tmp_path / "ledger.jsonl", 5.0).spent() == pytest.approx(0.10)


async def test_budget_blocks_the_call_before_it_is_sent(tmp_path: Path) -> None:
    (tmp_path / "ledger.jsonl").write_text(json.dumps({"cost_usd": 4.9999}) + "\n")
    llm, fake = make_client(tmp_path, lambda kw: message(parsed=PARSED))
    big = CallSpec(node="draft", model="claude-sonnet-5-5", system="s", user="u", max_tokens=16_000)
    with pytest.raises(BudgetExceededError):
        await llm.text(big)
    assert fake.messages.calls == []


async def test_failed_call_releases_its_reservation(tmp_path: Path) -> None:
    llm, _ = make_client(tmp_path, lambda kw: api_status_error(529), cap=0.01)
    with pytest.raises(anthropic.APIStatusError):
        await llm.parse(SPEC, ClassifierOutput)
    assert llm.ledger._reserved == 0.0
    assert llm.ledger.spent() == 0.0


@pytest.mark.parametrize(
    ("msg", "match"),
    [
        (message(stop_reason="refusal"), "refused"),
        (message(text="partial", stop_reason="max_tokens"), "truncated"),
        (message(parsed=None), "structured output"),
    ],
)
async def test_unusable_responses_raise(tmp_path: Path, msg: Any, match: str) -> None:
    llm, _ = make_client(tmp_path, lambda kw: msg)
    with pytest.raises(LLMResponseError, match=match):
        await llm.parse(SPEC, ClassifierOutput)


async def test_empty_text_raises(tmp_path: Path) -> None:
    llm, _ = make_client(tmp_path, lambda kw: message(text="   "))
    with pytest.raises(LLMResponseError, match="empty"):
        await llm.text(
            CallSpec(node="draft", model="claude-sonnet-5-5", system="s", user="u", max_tokens=10)
        )


async def test_unknown_model_has_no_price(tmp_path: Path) -> None:
    llm, _ = make_client(tmp_path, lambda kw: message(text="x"))
    with pytest.raises(LLMResponseError, match="no price"):
        await llm.text(CallSpec(node="n", model="mystery", system="s", user="u", max_tokens=10))


def test_missing_key_raises_only_when_used(tmp_path: Path) -> None:
    llm = LLMClient(Settings(ledger_path=tmp_path / "l.jsonl", anthropic_api_key=None))
    with pytest.raises(MissingAPIKeyError):
        _ = llm.client


@pytest.mark.parametrize(
    ("exc", "transient"),
    [
        (api_status_error(429), True),
        (api_status_error(500), True),
        (api_status_error(503), True),
        (api_status_error(529), True),
        (api_status_error(400), False),
        (api_status_error(401), False),
        (api_status_error(404), False),
        (NodeTimeoutError(node="classify", elapsed=45.0, kind="run", run_timeout=45.0), True),
        (LLMResponseError("refused"), False),
        (ValueError("bug"), False),
    ],
)
def test_is_transient(exc: BaseException, transient: bool) -> None:
    assert is_transient(exc) is transient
