"""Thin async wrapper over the Anthropic SDK.

Responsibilities, and nothing else:
- one place that turns ``usage`` into tokens, cache hits and dollars;
- a persistent spend ledger that refuses a call *before* it could push spend past the cap;
- a cache breakpoint on the static system prompt;
- one structured JSON log line per call.

Retries are deliberately **not** done here: the SDK client is built with ``max_retries=0`` and the
LangGraph node ``RetryPolicy`` is the single retry layer (DECISIONS D3). ``is_transient`` is the
predicate that policy uses.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypeVar

import anthropic
from anthropic import AsyncAnthropic
from langgraph.errors import NodeTimeoutError
from pydantic import BaseModel

from fielddesk.config import ModelPrice, Settings
from fielddesk.schemas import NodeMetrics

logger = logging.getLogger("fielddesk")

T = TypeVar("T", bound=BaseModel)

ThinkingMode = Literal["disabled", "adaptive", "between_tools"]
Effort = Literal["low", "medium", "high"]

# Rough upper bound on tokens per character, used only for the pre-flight budget check.
_TOKENS_PER_CHAR_UPPER = 0.5


class BudgetExceededError(RuntimeError):
    """Raised before a call that could take total spend past ``max_spend_usd``."""


class LLMResponseError(RuntimeError):
    """The API answered, but not with something usable (refusal, truncation, unparsable)."""


class MissingAPIKeyError(RuntimeError):
    pass


_TRANSIENT = (
    anthropic.RateLimitError,
    anthropic.InternalServerError,
    anthropic.OverloadedError,
    anthropic.ServiceUnavailableError,
    anthropic.APIConnectionError,  # includes APITimeoutError
    NodeTimeoutError,
)


def is_transient(exc: BaseException) -> bool:
    """Retry 429, 5xx (incl. 529 overloaded), connection errors and timeouts. Never a 4xx."""
    if isinstance(exc, _TRANSIENT):
        return True
    return isinstance(exc, anthropic.APIStatusError) and exc.status_code >= 500


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
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, sort_keys=True) + "\n")
            self._spent = self.spent() + float(entry["cost_usd"])


@dataclass(frozen=True)
class CallSpec:
    node: str
    model: str
    system: str
    user: str
    max_tokens: int
    thinking: ThinkingMode = "disabled"
    effort: Effort | None = None


class LLMClient:
    def __init__(
        self,
        settings: Settings,
        client: AsyncAnthropic | None = None,
        ledger: SpendLedger | None = None,
    ) -> None:
        self.settings = settings
        self._client = client
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

    def price(self, model: str) -> ModelPrice:
        try:
            return self.settings.prices[model]
        except KeyError:
            raise LLMResponseError(f"no price configured for model {model!r}") from None

    async def parse(self, spec: CallSpec, output_format: type[T]) -> tuple[T, NodeMetrics]:
        message, metrics = await self._call(spec, output_format)
        parsed = getattr(message, "parsed_output", None)
        if not isinstance(parsed, output_format):
            raise LLMResponseError(f"{spec.node}: structured output missing or invalid")
        return parsed, metrics

    async def text(self, spec: CallSpec) -> tuple[str, NodeMetrics]:
        message, metrics = await self._call(spec, None)
        text = "".join(b.text for b in message.content if getattr(b, "type", None) == "text")
        if not text.strip():
            raise LLMResponseError(f"{spec.node}: empty text response")
        return text.strip(), metrics

    async def _call(
        self, spec: CallSpec, output_format: type[BaseModel] | None
    ) -> tuple[Any, NodeMetrics]:
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

        started = time.perf_counter()
        entry: dict[str, Any] | None = None
        try:
            if output_format is not None:
                message = await self.client.messages.parse(output_format=output_format, **kwargs)
            else:
                message = await self.client.messages.create(**kwargs)
            latency_ms = (time.perf_counter() - started) * 1000
            u = message.usage
            metrics = NodeMetrics(
                node=spec.node,
                model_id=spec.model,
                input_tokens=u.input_tokens,
                output_tokens=u.output_tokens,
                cache_read_tokens=u.cache_read_input_tokens or 0,
                cache_write_tokens=u.cache_creation_input_tokens or 0,
                latency_ms=round(latency_ms, 1),
            )
            metrics.cost_usd = round(
                cost_usd(
                    price,
                    input_tokens=metrics.input_tokens,
                    output_tokens=metrics.output_tokens,
                    cache_read=metrics.cache_read_tokens,
                    cache_write=metrics.cache_write_tokens,
                ),
                8,
            )
            entry = {
                "ts": datetime.now(UTC).isoformat(),
                "node": spec.node,
                "model": spec.model,
                "cost_usd": metrics.cost_usd,
                "request_id": getattr(message, "_request_id", None),
            }
        finally:
            await self.ledger.settle(worst, entry)

        log_event("llm_call", **metrics.model_dump(), stop_reason=message.stop_reason)
        if message.stop_reason == "refusal":
            raise LLMResponseError(f"{spec.node}: model refused the request")
        if message.stop_reason == "max_tokens":
            raise LLMResponseError(f"{spec.node}: output truncated at max_tokens")
        return message, metrics
