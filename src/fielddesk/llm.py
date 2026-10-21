"""Thin async LLM wrapper with two backends, chosen per model ID:

- a bare Claude ID (``claude-haiku-5-5``) goes to the Anthropic SDK;
- a namespaced ID (``google/gemma-4-31b-it:free``) goes to OpenRouter's chat-completions API.

Responsibilities, and nothing else:
- one place that turns provider usage into tokens, cache hits and dollars;
- a persistent spend ledger that refuses a call *before* it could push spend past the cap;
- a cache breakpoint on the static system prompt (Anthropic);
- client-side pacing for OpenRouter's free-tier per-minute limit;
- one structured JSON log line per call.

Retries are deliberately **not** done here. The Anthropic client is built with ``max_retries=0``
and the LangGraph node ``RetryPolicy`` is the single retry layer (DECISIONS D3).
``is_transient`` is the predicate that policy uses, for both backends.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypeVar

import anthropic
import httpx
from anthropic import AsyncAnthropic
from langgraph.errors import NodeTimeoutError
from pydantic import BaseModel, ValidationError

from fielddesk.config import ModelPrice, Settings
from fielddesk.schemas import NodeMetrics

logger = logging.getLogger("fielddesk")

T = TypeVar("T", bound=BaseModel)

ThinkingMode = Literal["disabled", "adaptive", "between_tools"]
Effort = Literal["low", "medium", "high"]
StopReason = Literal["end_turn", "max_tokens", "refusal", "other"]

# Rough upper bound on tokens per character, used only for the pre-flight budget check.
_TOKENS_PER_CHAR_UPPER = 0.5
_FREE = ModelPrice(input=0.0, output=0.0, cache_read=0.0, cache_write=0.0)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


class BudgetExceededError(RuntimeError):
    """Raised before a call that could take total spend past ``max_spend_usd``."""


class LLMResponseError(RuntimeError):
    """The API answered, but not with something usable (refusal, truncation, unparsable)."""


class MissingAPIKeyError(RuntimeError):
    pass


class OpenRouterHTTPError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"OpenRouter HTTP {status_code}: {message[:200]}")
        self.status_code = status_code


_TRANSIENT = (
    anthropic.RateLimitError,
    anthropic.InternalServerError,
    anthropic.OverloadedError,
    anthropic.ServiceUnavailableError,
    anthropic.APIConnectionError,  # includes APITimeoutError
    httpx.TimeoutException,
    httpx.TransportError,
    TimeoutError,  # our wall-clock cap (asyncio.timeout)
    NodeTimeoutError,
)


def is_transient(exc: BaseException) -> bool:
    """Retry 408/429, 5xx (incl. 529 overloaded), connection errors and timeouts. Never another
    4xx, a refusal, or an unparsable answer: retrying those pays again for the same failure."""
    if isinstance(exc, _TRANSIENT):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        return exc.status_code >= 500
    if isinstance(exc, OpenRouterHTTPError):
        return exc.status_code in (408, 429) or exc.status_code >= 500
    return False


def log_event(event: str, **fields: Any) -> None:
    """One JSON object per line; never pass secrets in ``fields``."""
    payload = {"ts": datetime.now(UTC).isoformat(), "event": event, **fields}
    logger.info(json.dumps(payload, default=str, sort_keys=True))


def cost_usd(
    price: ModelPrice, *, input_tokens: int, output_tokens: int, cache_read: int, cache_write: int
) -> float:
    return (
        input_tokens * price.input
        + output_tokens * price.output
        + cache_read * price.cache_read
        + cache_write * price.cache_write
    ) / 1_000_000


def is_openrouter(model: str) -> bool:
    return "/" in model


class SpendLedger:
    """Append-only JSONL of every billed call. Survives restarts, so the cap is chapter-wide.

    Single-process only: concurrent writers in one process are serialized by an asyncio lock;
    two FieldDesk processes running at once could each pass the check (documented limitation).
    """

    def __init__(self, path: Path, cap_usd: float) -> None:
        self.path = path
        self.cap_usd = cap_usd
        self._lock = asyncio.Lock()
        self._spent: float | None = None
        self._reserved = 0.0

    def spent(self) -> float:
        if self._spent is None:
            total = 0.0
            if self.path.exists():
                for line in self.path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        total += float(json.loads(line)["cost_usd"])
            self._spent = total
        return self._spent

    async def reserve(self, worst_case_usd: float) -> None:
        async with self._lock:
            projected = self.spent() + self._reserved + worst_case_usd
            if projected > self.cap_usd:
                raise BudgetExceededError(
                    f"call could bring spend to ${projected:.4f}, over the "
                    f"${self.cap_usd:.2f} cap (spent ${self.spent():.4f})"
                )
            self._reserved += worst_case_usd

    async def settle(self, worst_case_usd: float, entry: dict[str, Any] | None) -> None:
        async with self._lock:
            self._reserved = max(0.0, self._reserved - worst_case_usd)
            if entry is None:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(entry, sort_keys=True) + "\n")
            self._spent = self.spent() + float(entry["cost_usd"])


@dataclass(frozen=True)
class CallSpec:
    node: str
    model: str
    system: str
    user: str
    max_tokens: int
    thinking: ThinkingMode = "disabled"  # Anthropic only
    effort: Effort | None = None  # Anthropic only
    timeout_s: float | None = None  # hard wall-clock cap; defaults to settings.node_run_timeout_s


@dataclass
class _Raw:
    """Provider-neutral view of one response."""

    text: str
    parsed: BaseModel | None
    stop_reason: StopReason
    input_tokens: int
    output_tokens: int
    cache_read: int
    cache_write: int
    request_id: str | None


class _Pacer:
    """Spaces requests at least ``interval`` seconds apart (OpenRouter free tier: ~20/min)."""

    def __init__(self, interval: float) -> None:
        self.interval = interval
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self) -> None:
        async with self._lock:
            delay = self._last + self.interval - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._last = time.monotonic()


class LLMClient:
    def __init__(
        self,
        settings: Settings,
        client: AsyncAnthropic | None = None,
        ledger: SpendLedger | None = None,
        openrouter_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._client = client
        self._http: httpx.AsyncClient | None = None
        self._transport = openrouter_transport
        self._pacer = _Pacer(settings.openrouter_min_interval_s)
        self.ledger = ledger or SpendLedger(settings.ledger_path, settings.max_spend_usd)

    @property
    def client(self) -> AsyncAnthropic:
        if self._client is None:
            key = self.settings.anthropic_api_key
            if key is None or not key.get_secret_value():
                raise MissingAPIKeyError("ANTHROPIC_API_KEY is not set (see .env.example)")
            self._client = AsyncAnthropic(
                api_key=key.get_secret_value(),
                max_retries=0,
                timeout=self.settings.node_run_timeout_s,
            )
        return self._client

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            key = self.settings.openrouter_api_key
            if key is None or not key.get_secret_value():
                raise MissingAPIKeyError("OPENROUTER_API_KEY is not set (see .env.example)")
            self._http = httpx.AsyncClient(
                base_url=self.settings.openrouter_base_url,
                headers={
                    "Authorization": f"Bearer {key.get_secret_value()}",
                    "X-Title": "FieldDesk",
                },
                timeout=self.settings.node_run_timeout_s,
                transport=self._transport,
            )
        return self._http

    def price(self, model: str) -> ModelPrice:
        if model in self.settings.prices:
            return self.settings.prices[model]
        if model.endswith(":free"):
            return _FREE
        raise LLMResponseError(f"no price configured for model {model!r}")

    async def parse(self, spec: CallSpec, output_format: type[T]) -> tuple[T, NodeMetrics]:
        raw, metrics = await self._call(spec, output_format)
        if not isinstance(raw.parsed, output_format):
            raise LLMResponseError(f"{spec.node}: structured output missing or invalid")
        return raw.parsed, metrics

    async def text(self, spec: CallSpec) -> tuple[str, NodeMetrics]:
        raw, metrics = await self._call(spec, None)
        if not raw.text.strip():
            raise LLMResponseError(f"{spec.node}: empty text response")
        return raw.text.strip(), metrics

    # --- shared accounting -------------------------------------------------------------------

    async def _call(
        self, spec: CallSpec, output_format: type[BaseModel] | None
    ) -> tuple[_Raw, NodeMetrics]:
        price = self.price(spec.model)
        est_input = int((len(spec.system) + len(spec.user)) * _TOKENS_PER_CHAR_UPPER) + 50
        # Worst case: nothing cached, the cache write billed on top, and max_tokens of output.
        worst = cost_usd(
            price,
            input_tokens=est_input,
            output_tokens=spec.max_tokens,
            cache_read=0,
            cache_write=est_input,
        )
        await self.ledger.reserve(worst)

        started = time.perf_counter()
        entry: dict[str, Any] | None = None
        try:
            if is_openrouter(spec.model):
                raw = await self._openrouter(spec, output_format)
            else:
                raw = await self._anthropic(spec, output_format)
            metrics = NodeMetrics(
                node=spec.node,
                model_id=spec.model,
                input_tokens=raw.input_tokens,
                output_tokens=raw.output_tokens,
                cache_read_tokens=raw.cache_read,
                cache_write_tokens=raw.cache_write,
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
            )
            metrics.cost_usd = round(
                cost_usd(
                    price,
                    input_tokens=raw.input_tokens,
                    output_tokens=raw.output_tokens,
                    cache_read=raw.cache_read,
                    cache_write=raw.cache_write,
                ),
                8,
            )
            entry = {
                "ts": datetime.now(UTC).isoformat(),
                "node": spec.node,
                "model": spec.model,
                "cost_usd": metrics.cost_usd,
                "request_id": raw.request_id,
            }
        finally:
            await self.ledger.settle(worst, entry)

        log_event("llm_call", **metrics.model_dump(), stop_reason=raw.stop_reason)
        if raw.stop_reason == "refusal":
            raise LLMResponseError(f"{spec.node}: model refused the request")
        if raw.stop_reason == "max_tokens":
            raise LLMResponseError(f"{spec.node}: output truncated at max_tokens")
        return raw, metrics

    # --- Anthropic ---------------------------------------------------------------------------

    async def _anthropic(self, spec: CallSpec, output_format: type[BaseModel] | None) -> _Raw:
        kwargs: dict[str, Any] = {
            "model": spec.model,
            "max_tokens": spec.max_tokens,
            "system": [
                {"type": "text", "text": spec.system, "cache_control": {"type": "ephemeral"}}
            ],
            "messages": [{"role": "user", "content": spec.user}],
            "thinking": {"type": spec.thinking},
        }
        if spec.effort is not None:
            kwargs["output_config"] = {"effort": spec.effort}
        if output_format is not None:
            message = await self.client.messages.parse(output_format=output_format, **kwargs)
        else:
            message = await self.client.messages.create(**kwargs)
        u = message.usage
        stop = message.stop_reason
        return _Raw(
            text="".join(getattr(b, "text", "") for b in message.content if b.type == "text"),
            parsed=getattr(message, "parsed_output", None),
            stop_reason=stop if stop in ("end_turn", "max_tokens", "refusal") else "other",
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_read=u.cache_read_input_tokens or 0,
            cache_write=u.cache_creation_input_tokens or 0,
            request_id=getattr(message, "_request_id", None),
        )

    # --- OpenRouter --------------------------------------------------------------------------

    def _response_format(
        self, model: str, output_format: type[BaseModel]
    ) -> tuple[dict[str, Any], str]:
        """(response_format, system suffix). Models with native structured outputs get the JSON
        Schema enforced; others get JSON mode plus the schema in the prompt, and Pydantic
        validation decides."""
        schema = output_format.model_json_schema()
        if model in self.settings.openrouter_json_schema_models:
            fmt = {
                "type": "json_schema",
                "json_schema": {"name": output_format.__name__, "schema": schema, "strict": True},
            }
            return fmt, ""
        suffix = (
            "\n\n## Output format\n\nReturn exactly one JSON object and nothing else (no prose, "
            "no code fences). It must validate against this JSON Schema:\n"
            + json.dumps(schema, sort_keys=True)
        )
        return {"type": "json_object"}, suffix

    async def _openrouter(self, spec: CallSpec, output_format: type[BaseModel] | None) -> _Raw:
        system = spec.system
        body: dict[str, Any] = {"model": spec.model, "max_tokens": spec.max_tokens}
        if output_format is not None:
            body["response_format"], suffix = self._response_format(spec.model, output_format)
            system += suffix
        body["messages"] = [
            {"role": "system", "content": system},
            {"role": "user", "content": spec.user},
        ]
        await self._pacer.wait()
        # httpx's timeout is per read, so a server that trickles bytes can hold a call open
        # indefinitely (JOURNAL 2026-10-21). This caps the whole request.
        async with asyncio.timeout(spec.timeout_s or self.settings.node_run_timeout_s):
            response = await self.http.post("/chat/completions", json=body)
        if response.status_code != 200:
            raise OpenRouterHTTPError(response.status_code, response.text)
        data = response.json()
        if "error" in data:  # OpenRouter can report upstream failures inside a 200
            err = data["error"]
            code = err.get("code", 502)
            raise OpenRouterHTTPError(
                code if isinstance(code, int) else 502, str(err.get("message"))
            )
        choice = data["choices"][0]
        text = choice["message"].get("content") or ""
        finish = choice.get("finish_reason") or choice.get("native_finish_reason") or "stop"
        stop_map: dict[str, StopReason] = {
            "stop": "end_turn",
            "length": "max_tokens",
            "content_filter": "refusal",
        }
        stop = stop_map.get(finish, "other")
        parsed = None
        if output_format is not None and stop == "end_turn":
            try:
                parsed = output_format.model_validate_json(_FENCE.sub("", text.strip()))
            except ValidationError as exc:
                raise LLMResponseError(
                    f"{spec.node}: {spec.model} returned JSON that fails the schema "
                    f"({exc.error_count()} errors)"
                ) from None
        usage = data.get("usage") or {}
        details = usage.get("prompt_tokens_details") or {}
        return _Raw(
            text=text,
            parsed=parsed,
            stop_reason=stop,
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            cache_read=int(details.get("cached_tokens") or 0),
            cache_write=0,
            request_id=data.get("id"),
        )
