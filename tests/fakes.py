"""A fake AsyncAnthropic. It answers from a script, so tests never touch the network."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2

Responder = Callable[[dict[str, Any]], Any]


def usage(
    inp: int = 600, out: int = 80, cache_read: int = 0, cache_write: int = 0
) -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=inp,
        output_tokens=out,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_write,
    )


def message(
    *,
    text: str = "",
    parsed: Any = None,
    stop_reason: str = "end_turn",
    u: SimpleNamespace | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)] if text else [],
        parsed_output=parsed,
        stop_reason=stop_reason,
        usage=u or usage(),
        _request_id="req_fake",
    )


def api_status_error(status: int) -> anthropic.APIStatusError:
    """The SDK typed error for a status code (429 -> RateLimitError, 529 -> OverloadedError)."""
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(status, request=request, json={"error": {"message": "fake"}})
    client = anthropic.Anthropic(api_key="test-not-a-key")
    return client._make_status_error("fake", body=None, response=response)


class FakeMessages:
    def __init__(self, responder: Responder) -> None:
        self.responder = responder
        self.calls: list[dict[str, Any]] = []

    async def _respond(self, kwargs: dict[str, Any]) -> Any:
        self.calls.append(kwargs)
        result = self.responder(kwargs)
        if isinstance(result, BaseException):
            raise result
        return result

    async def parse(self, **kwargs: Any) -> Any:
        return await self._respond(kwargs)

    async def create(self, **kwargs: Any) -> Any:
        return await self._respond(kwargs)


class FakeAnthropic:
    def __init__(self, responder: Responder) -> None:
        self.messages = FakeMessages(responder)
