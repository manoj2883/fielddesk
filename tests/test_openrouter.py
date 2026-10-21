"""OpenRouter backend against an httpx MockTransport: request shape, parsing, errors, pacing,
and the full graph running on free-model IDs."""

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from fielddesk.config import Settings
from fielddesk.graph import TriageAgent
from fielddesk.llm import (
    CallSpec,
    LLMClient,
    LLMResponseError,
    MissingAPIKeyError,
    OpenRouterHTTPError,
    is_transient,
)
from fielddesk.schemas import ClassifierOutput, EscalationReason, TicketInput

CLASSIFY = "minimax/minimax-m3.1-flash:free"
SCHEMA_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"
DRAFT = "google/gemma-4-31b-it:free"
GOOD = {
    "category": "technical_fault",
    "priority": "high",
    "confidence": 0.9,
    "safety_flag": False,
    "rationale": "RX-E214 and no shifting",
}


def completion(content: str, finish: str = "stop", cached: int = 0) -> dict[str, Any]:
    return {
        "id": "gen-123",
        "choices": [{"message": {"content": content}, "finish_reason": finish}],
        "usage": {
            "prompt_tokens": 700,
            "completion_tokens": 60,
            "prompt_tokens_details": {"cached_tokens": cached},
        },
    }


class Recorder:
    def __init__(self, *responses: httpx.Response | dict[str, Any]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer test-or-key"
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        return r if isinstance(r, httpx.Response) else httpx.Response(200, json=r)


def make(tmp_path: Path, rec: Recorder, **kw: Any) -> LLMClient:
    s = Settings(
        openrouter_api_key="test-or-key",
        anthropic_api_key=None,
        ledger_path=tmp_path / "ledger.jsonl",
        openrouter_min_interval_s=0.0,
        classify_model=CLASSIFY,
        draft_model=DRAFT,
        **kw,
    )
    return LLMClient(s, openrouter_transport=httpx.MockTransport(rec))


def spec(model: str = CLASSIFY) -> CallSpec:
    return CallSpec(node="classify", model=model, system="SYSTEM", user="ticket", max_tokens=300)


async def test_json_mode_puts_schema_in_prompt_and_parses_fenced_json(tmp_path: Path) -> None:
    rec = Recorder(completion("```json\n" + json.dumps(GOOD) + "\n```", cached=500))
    out, metrics = await make(tmp_path, rec).parse(spec(), ClassifierOutput)
    assert out.category.value == "technical_fault"
    body = rec.requests[0]
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["role"] == "system"
    assert "JSON Schema" in body["messages"][0]["content"]
    assert "temperature" not in body
    assert metrics.cost_usd == 0.0  # free model
    assert (metrics.input_tokens, metrics.cache_read_tokens) == (700, 500)


async def test_schema_models_get_native_json_schema(tmp_path: Path) -> None:
    rec = Recorder(completion(json.dumps(GOOD)))
    await make(tmp_path, rec).parse(spec(SCHEMA_MODEL), ClassifierOutput)
    fmt = rec.requests[0]["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["name"] == "ClassifierOutput"
    assert "JSON Schema" not in rec.requests[0]["messages"][0]["content"]


async def test_text_call(tmp_path: Path) -> None:
    rec = Recorder(completion("Hello dealer"))
    text, _ = await make(tmp_path, rec).text(spec(DRAFT))
    assert text == "Hello dealer"
    assert "response_format" not in rec.requests[0]


@pytest.mark.parametrize(
    ("response", "exc", "match"),
    [
        (completion("not json at all"), LLMResponseError, "fails the schema"),
        (completion(json.dumps({**GOOD, "confidence": 3})), LLMResponseError, "fails the schema"),
        (completion("{}", finish="length"), LLMResponseError, "truncated"),
        (completion("", finish="content_filter"), LLMResponseError, "refused"),
        (httpx.Response(429, text="rate limited"), OpenRouterHTTPError, "429"),
        ({"error": {"code": 502, "message": "upstream down"}}, OpenRouterHTTPError, "502"),
    ],
)
async def test_failures_raise_typed_errors(
    tmp_path: Path, response: Any, exc: type[Exception], match: str
) -> None:
    with pytest.raises(exc, match=match):
        await make(tmp_path, Recorder(response)).parse(spec(), ClassifierOutput)


@pytest.mark.parametrize(
    ("status", "transient"),
    [(408, True), (429, True), (500, True), (502, True), (400, False), (401, False), (402, False)],
)
def test_openrouter_status_transience(status: int, transient: bool) -> None:
    assert is_transient(OpenRouterHTTPError(status, "x")) is transient


def test_network_errors_are_transient() -> None:
    assert is_transient(httpx.ConnectError("boom"))
    assert is_transient(httpx.ReadTimeout("slow"))


async def test_missing_key(tmp_path: Path) -> None:
    llm = LLMClient(Settings(openrouter_api_key=None, ledger_path=tmp_path / "l.jsonl"))
    with pytest.raises(MissingAPIKeyError, match="OPENROUTER_API_KEY"):
        await llm.text(spec(DRAFT))


async def test_pacer_spaces_requests(tmp_path: Path) -> None:
    rec = Recorder(completion("hi"))
    llm = make(tmp_path, rec)
    llm._pacer.interval = 0.15
    started = time.monotonic()
    for _ in range(3):
        await llm.text(spec(DRAFT))
    assert time.monotonic() - started >= 0.3


async def test_unpriced_paid_openrouter_model_is_refused(tmp_path: Path) -> None:
    with pytest.raises(LLMResponseError, match="no price"):
        await make(tmp_path, Recorder(completion("x"))).text(spec("vendor/paid-model"))


async def test_full_graph_on_free_models(tmp_path: Path) -> None:
    rec = Recorder(
        completion(json.dumps(GOOD)),
        completion("Thanks for the RX-E214 report. Could you send the machine hours?"),
    )
    llm = make(tmp_path, rec, retry_initial_interval_s=0.01)
    agent = TriageAgent(llm.settings, llm)
    ticket = TicketInput(
        ticket_id="T-1",
        dealer_name="d",
        machine_model="Ridgeline T680",
        subject="RX-E214",
        message="Throws RX-E214 and will not shift.",
        error_code="RX-E214",
    )
    r = await agent.run(ticket)
    assert not r.escalate, r.escalation_detail
    assert r.model_ids == {"classify": CLASSIFY, "draft": DRAFT}
    assert [req["model"] for req in rec.requests] == [CLASSIFY, DRAFT]


async def test_free_model_rate_limit_ends_in_llm_error(tmp_path: Path) -> None:
    rec = Recorder(httpx.Response(429, text="daily limit"))
    llm = make(tmp_path, rec, retry_initial_interval_s=0.01)
    r = await TriageAgent(llm.settings, llm).run(
        TicketInput(ticket_id="T-2", dealer_name="d", machine_model="m", subject="s", message="m")
    )
    assert r.escalation_reason is EscalationReason.LLM_ERROR
    assert len(rec.requests) == 3  # retried as transient, then gave up cleanly


async def test_hard_timeout_caps_a_trickling_server(tmp_path: Path) -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(2)
        return httpx.Response(200, json=completion("late"))

    s = Settings(
        openrouter_api_key="test-or-key",
        ledger_path=tmp_path / "ledger.jsonl",
        openrouter_min_interval_s=0.0,
    )
    llm = LLMClient(s, openrouter_transport=httpx.MockTransport(slow))
    call = CallSpec(
        node="generate", model=DRAFT, system="s", user="u", max_tokens=10, timeout_s=0.1
    )
    started = time.monotonic()
    with pytest.raises(TimeoutError) as info:
        await llm.text(call)
    assert time.monotonic() - started < 1.0
    assert is_transient(info.value)
