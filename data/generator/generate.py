"""Spec -> ticket text with Claude. Every call is cached on disk, so reruns cost $0."""

from __future__ import annotations

import asyncio
import json
import random
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from data.generator.leakage import check_text
from data.generator.render import render
from data.generator.spec import ScenarioSpec
from fielddesk.config import REPO_ROOT, Settings
from fielddesk.llm import CallSpec, LLMClient, is_transient, log_event
from fielddesk.schemas import LabeledTicket, TicketInput

GEN_PROMPT_VERSION = "generate_v1"
PROMPT_DIR = Path(__file__).parent / "prompts"
CACHE_DIR = REPO_ROOT / ".cache" / "generations"
MAX_ATTEMPTS = 4


class GeneratedText(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str = Field(max_length=120)
    message: str = Field(max_length=3000)


@dataclass
class GenOutcome:
    spec: ScenarioSpec
    row: LabeledTicket | None
    attempts: int
    cost_usd: float
    cache_hits: int
    rejections: list[list[str]]


def _cache_path(spec: ScenarioSpec, attempt: int, model: str) -> Path:
    return CACHE_DIR / f"{spec.spec_hash}_{GEN_PROMPT_VERSION}_{model}_a{attempt}.json"


def to_ticket(spec: ScenarioSpec, text: GeneratedText) -> TicketInput:
    s = spec.surface
    return TicketInput(
        ticket_id=spec.ticket_id,
        dealer_name=s.dealer,
        machine_model=s.machine_model,
        subject=text.subject,
        message=text.message,
        error_code=s.error_code if s.code_in_field else None,
        warranty_status=s.warranty_status_field,
        machine_hours=s.machine_hours_true if s.hours_in_field else None,
    )


async def _call_with_retry(llm: LLMClient, call: CallSpec) -> tuple[GeneratedText, float]:
    for attempt in range(4):
        try:
            text, metrics = await llm.parse(call, GeneratedText)
            return text, metrics.cost_usd
        except Exception as exc:
            if not is_transient(exc) or attempt == 3:
                raise
            await asyncio.sleep(2**attempt + random.random())
    raise AssertionError("unreachable")


async def generate_one(spec: ScenarioSpec, llm: LLMClient, settings: Settings) -> GenOutcome:
    system = (PROMPT_DIR / f"{GEN_PROMPT_VERSION}.md").read_text(encoding="utf-8")
    cost, hits, rejections = 0.0, 0, []
    for attempt in range(MAX_ATTEMPTS):
        path = _cache_path(spec, attempt, settings.generator_model)
        if path.exists():
            text = GeneratedText.model_validate_json(path.read_text(encoding="utf-8"))
            hits += 1
        else:
            call = CallSpec(
                node="generate",
                model=settings.generator_model,
                system=system,
                user=render(spec, attempt),
                max_tokens=1200,
                thinking="between_tools",
                effort="medium",
            )
            text, c = await _call_with_retry(llm, call)
            cost += c
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text.model_dump_json(), encoding="utf-8", newline="\n")

        check = check_text(
            f"{text.subject}\n{text.message}", spec.surface.error_code, spec.surface.injection_text
        )
        if check.ok:
            row = LabeledTicket(
                ticket=to_ticket(spec, text), label=spec.label, spec_hash=spec.spec_hash
            )
            return GenOutcome(spec, row, attempt + 1, cost, hits, rejections)
        rejections.append(list(check.reasons))
        log_event(
            "generation_rejected", ticket_id=spec.ticket_id, attempt=attempt, reasons=check.reasons
        )
    return GenOutcome(spec, None, MAX_ATTEMPTS, cost, hits, rejections)


async def generate_all(
    specs: list[ScenarioSpec], settings: Settings, concurrency: int = 4
) -> list[GenOutcome]:
    llm = LLMClient(settings)
    sem = asyncio.Semaphore(concurrency)

    async def guarded(spec: ScenarioSpec) -> GenOutcome:
        async with sem:
            return await generate_one(spec, llm, settings)

    return await asyncio.gather(*(guarded(s) for s in specs))


def write_jsonl(path: Path, rows: list[BaseModel] | list[dict[str, object]]) -> None:
    """LF line endings on every OS, so file hashes are stable (JOURNAL S3)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            line = (
                row.model_dump_json()
                if isinstance(row, BaseModel)
                else json.dumps(row, sort_keys=True)
            )
            fh.write(line + "\n")
