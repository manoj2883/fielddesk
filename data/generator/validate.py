"""S6: generated rows -> checked, deduplicated, blind-validated, split and locked dataset.

1. Pydantic-validate every row of ``data/generated.jsonl``.
2. Drop near-duplicate messages (rapidfuzz ``token_set_ratio`` >= ``dedup_threshold``).
3. A model from another family labels a stratified sample *blind* (ticket text only). Results
   are cached per ticket, so a run stopped by the free tier's daily cap resumes for free.
4. Every disagreement with the spec label goes to ``data/review_queue.jsonl``. Labels are never
   changed here (LABELING_GUIDE).
5. Stratified dev/test split, ``TEST_SET_LOCK`` holding the test file's SHA-256, and
   ``dataset_card.md``.

The split depends only on the deduplicated rows and the seed, never on validation, so a resumed
validation run rewrites an identical test file and the lock still holds.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from rapidfuzz import fuzz, utils
from sklearn.metrics import cohen_kappa_score

from data.generator.generate import GEN_PROMPT_VERSION, _safe, call_with_retry, write_jsonl
from data.generator.spec import GENERATOR_SPEC_VERSION
from fielddesk.config import REPO_ROOT, Settings
from fielddesk.llm import CallSpec, LLMClient, is_transient, log_event
from fielddesk.schemas import Category, LabeledTicket, Priority

VALIDATE_PROMPT_VERSION = "validate_v1"
PROMPT_DIR = Path(__file__).parent / "prompts"
CACHE_DIR = REPO_ROOT / ".cache" / "validations"
VALIDATOR_SAMPLE_N = 50  # plan approval 2026-10-20: 50 of 200, the rest later by rerunning
TEST_FRACTION = 0.25  # 150 / 50
VALIDATOR_MAX_TOKENS = 2048
VALIDATOR_TIMEOUT_S = 120.0
_norm = utils.default_process
FIELDS = ("category", "priority", "needs_human", "safety_risk")


class ValidatorLabel(BaseModel):
    """What the blind validator returns. No difficulty: that is analysis metadata, not a label."""

    model_config = ConfigDict(extra="forbid")
    category: Category
    priority: Priority
    needs_human: bool
    safety_risk: bool
    rationale: str = Field(max_length=600)


@dataclass
class Validated:
    ticket_id: str
    label: ValidatorLabel | None
    model: str | None
    error: str | None = None


@dataclass
class Report:
    rows_in: int = 0
    schema_errors: list[str] = field(default_factory=list)
    duplicates: list[tuple[str, str, float]] = field(default_factory=list)  # (dropped, kept, score)
    validated: list[Validated] = field(default_factory=list)
    sample_ids: list[str] = field(default_factory=list)
    paused: bool = False


# --- 1. load -------------------------------------------------------------------------------


def load_rows(path: Path) -> tuple[list[LabeledTicket], list[str]]:
    rows, errors = [], []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(LabeledTicket.model_validate_json(line))
        except ValidationError as exc:
            errors.append(f"line {n}: {exc.error_count()} schema errors")
    return rows, errors


# --- 2. dedup ------------------------------------------------------------------------------


def dedup(
    rows: Sequence[LabeledTicket], threshold: float
) -> tuple[list[LabeledTicket], list[tuple[str, str, float]]]:
    """Keep the first of any near-duplicate pair, in ticket order."""
    kept: list[LabeledTicket] = []
    dropped: list[tuple[str, str, float]] = []
    for row in sorted(rows, key=lambda r: r.ticket.ticket_id):
        # default_process lowercases and strips punctuation: "Kit." and "kit!" are the same word.
        scored = (
            (fuzz.token_set_ratio(row.ticket.message, k.ticket.message, processor=_norm), k)
            for k in kept
        )
        best = max(scored, key=lambda pair: pair[0], default=(0.0, None))
        if best[1] is not None and best[0] >= threshold:
            dropped.append((row.ticket.ticket_id, best[1].ticket.ticket_id, round(best[0], 1)))
        else:
            kept.append(row)
    return kept, dropped


# --- stratified selection (validator sample and split share it) -------------------------------


def stratified_pick[R, K: Hashable](
    items: Sequence[R], n: int, key: Callable[[R], K], seed: int
) -> list[R]:
    """Pick ``n`` items with each stratum represented in proportion (largest remainder),
    seeded and independent of input order."""
    if n >= len(items):
        return list(items)
    strata: dict[K, list[R]] = defaultdict(list)
    for item in items:
        strata[key(item)].append(item)
    order = sorted(strata, key=repr)
    quotas = {k: n * len(strata[k]) / len(items) for k in order}
    take = {k: math.floor(q) for k, q in quotas.items()}
    for k in sorted(order, key=lambda k: (-(quotas[k] - take[k]), repr(k)))[
        : n - sum(take.values())
    ]:
        take[k] += 1
    rng = random.Random(seed)
    picked: list[R] = []
    for k in order:
        pool = sorted(strata[k], key=repr)
        rng.shuffle(pool)
        picked.extend(pool[: take[k]])
    return picked


def _stratum(row: LabeledTicket) -> tuple[str, str]:
    return row.label.category.value, row.label.difficulty.value


def split(
    rows: Sequence[LabeledTicket], seed: int
) -> tuple[list[LabeledTicket], list[LabeledTicket]]:
    by_id = sorted(rows, key=lambda r: r.ticket.ticket_id)
    test_n = round(len(by_id) * TEST_FRACTION)
    test_ids = {r.ticket.ticket_id for r in stratified_pick(by_id, test_n, _stratum, seed)}
    dev = [r for r in by_id if r.ticket.ticket_id not in test_ids]
    test = [r for r in by_id if r.ticket.ticket_id in test_ids]
    return dev, test


# --- 3. blind validator --------------------------------------------------------------------


def system_prompt() -> str:
    guide = (REPO_ROOT / "data" / "LABELING_GUIDE.md").read_text(encoding="utf-8")
    return (
        (PROMPT_DIR / f"{VALIDATE_PROMPT_VERSION}.md")
        .read_text(encoding="utf-8")
        .replace("{guide}", guide)
    )


def blind_view(row: LabeledTicket) -> str:
    """Only what a dealer submitted. No label, no spec, no ticket ID."""
    t = row.ticket.model_dump(mode="json", exclude={"schema_version", "ticket_id"})
    return json.dumps(t, indent=2, ensure_ascii=False)


def _cache_path(row: LabeledTicket, model: str) -> Path:
    content = hashlib.sha256((system_prompt() + blind_view(row)).encode()).hexdigest()[:12]
    name = f"{row.ticket.ticket_id}_{VALIDATE_PROMPT_VERSION}_{_safe(model)}_{content}.json"
    return CACHE_DIR / name


class PausedError(RuntimeError):
    """Every validator model is rate-limited (likely the free daily cap). Rerun later."""


async def validate_one(row: LabeledTicket, llm: LLMClient, settings: Settings) -> Validated:
    models = (settings.validator_model, settings.validator_fallback_model)
    for model in models:
        path = _cache_path(row, model)
        if path.exists():
            label = ValidatorLabel.model_validate_json(path.read_text(encoding="utf-8"))
            return Validated(row.ticket.ticket_id, label, model)
    errors: list[str] = []
    all_transient = True
    system = system_prompt()
    for model in models:
        call = CallSpec(
            node="validate",
            model=model,
            system=system,
            user=blind_view(row),
            max_tokens=VALIDATOR_MAX_TOKENS,
            timeout_s=VALIDATOR_TIMEOUT_S,
        )
        try:
            label, _ = await call_with_retry(llm, call, ValidatorLabel)
        except Exception as exc:
            all_transient = all_transient and is_transient(exc)
            errors.append(f"{model}: {type(exc).__name__}: {str(exc)[:120]}")
            log_event("validation_failed", ticket_id=row.ticket.ticket_id, model=model)
            continue
        path = _cache_path(row, model)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(label.model_dump_json(), encoding="utf-8", newline="\n")
        return Validated(row.ticket.ticket_id, label, model)
    if all_transient:
        raise PausedError("; ".join(errors))
    return Validated(row.ticket.ticket_id, None, None, error="; ".join(errors))


async def validate_all(
    rows: Sequence[LabeledTicket], llm: LLMClient, settings: Settings
) -> tuple[list[Validated], bool]:
    """Sequential on purpose: the free tier is paced at ~20/min anyway, and stopping at the first
    rate-limit wall keeps the rest of the daily quota for the next run."""
    done: list[Validated] = []
    for row in rows:
        try:
            done.append(await validate_one(row, llm, settings))
        except PausedError as exc:
            log_event("validation_paused", ticket_id=row.ticket.ticket_id, reason=str(exc)[:300])
            return done, True
    return done, False


# --- 4. disagreements and agreement --------------------------------------------------------


def disagreements(row: LabeledTicket, v: ValidatorLabel) -> list[str]:
    return [f for f in FIELDS if getattr(row.label, f) != getattr(v, f)]


def review_entries(
    rows: dict[str, LabeledTicket], validated: Sequence[Validated], test_ids: set[str]
) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for v in validated:
        if v.label is None:
            continue
        row = rows[v.ticket_id]
        diff = disagreements(row, v.label)
        if diff:
            out.append(
                {
                    "ticket_id": v.ticket_id,
                    "split": "test" if v.ticket_id in test_ids else "dev",
                    "disagreements": diff,
                    "spec_label": {f: getattr(row.label, f) for f in FIELDS}
                    | {"difficulty": row.label.difficulty, "tricky_type": row.label.tricky_type},
                    "validator_label": v.label.model_dump(mode="json"),
                    "validator_model": v.model,
                    "status": "open",
                }
            )
    return json.loads(json.dumps(out, default=str))


def kappas(
    rows: dict[str, LabeledTicket], validated: Sequence[Validated]
) -> dict[str, float | None]:
    """Cohen's kappa per field; None when it is undefined (one class only on both sides)."""
    done = [v for v in validated if v.label is not None]
    out: dict[str, float | None] = {}
    for f in FIELDS:
        a = [str(getattr(rows[v.ticket_id].label, f)) for v in done]
        b = [str(getattr(v.label, f)) for v in done]
        k = float(cohen_kappa_score(a, b)) if len(set(a) | set(b)) > 1 else None
        out[f] = None if k is None or math.isnan(k) else round(k, 3)
    return out


# --- 5. lock and card ----------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dist(rows: Sequence[LabeledTicket], f: str) -> Counter[str]:
    return Counter(
        str(
            getattr(r.label, f).value
            if hasattr(getattr(r.label, f), "value")
            else getattr(r.label, f)
        )
        for r in rows
    )


def render_card(
    report: Report,
    dev: Sequence[LabeledTicket],
    test: Sequence[LabeledTicket],
    queue: Sequence[dict[str, object]],
    kappa: dict[str, float | None],
    hashes: dict[str, str],
    settings: Settings,
) -> str:
    done = [v for v in report.validated if v.label is not None]
    failed = [v for v in report.validated if v.label is None]
    by_model = Counter(v.model for v in done)
    lines = [
        "# Dataset card: FieldDesk Chapter 1 tickets",
        "",
        "Generated by `make validate-data` from `data/generated.jsonl`. Do not edit by hand.",
        "",
        "## Provenance",
        "",
        f"- Spec: `{GENERATOR_SPEC_VERSION}`, seed {settings.seed}. Labels come from the spec via `fielddesk.labeling`, never from a model.",
        f"- Generator: `{settings.generator_model}`, prompt `{GEN_PROMPT_VERSION}`.",
        f"- Blind validator: `{settings.validator_model}` (fallback `{settings.validator_fallback_model}`), prompt `{VALIDATE_PROMPT_VERSION}`. It sees the dealer's fields and message only.",
        "- All dealers, machines, codes and people are fictional (Ridgeline Equipment).",
        "",
        "## Pipeline counts",
        "",
        "| Stage | Count |",
        "|---|---|",
        f"| rows in `generated.jsonl` | {report.rows_in} |",
        f"| schema errors | {len(report.schema_errors)} |",
        f"| near-duplicates dropped (token_set_ratio >= {settings.dedup_threshold:g}) | {len(report.duplicates)} |",
        f"| final rows | {len(dev) + len(test)} |",
        f"| dev / test | {len(dev)} / {len(test)} |",
        "",
    ]
    if report.duplicates:
        lines += [
            "Dropped as near-duplicates: "
            + ", ".join(f"{d} (of {k}, {s:g})" for d, k, s in report.duplicates),
            "",
        ]
    lines += [
        "## Label distributions",
        "",
    ]
    for f in ("category", "priority", "difficulty", "needs_human", "safety_risk"):
        d_dev, d_test = _dist(dev, f), _dist(test, f)
        lines += [f"**{f}**", "", "| value | dev | test |", "|---|---|---|"]
        for value in sorted(set(d_dev) | set(d_test)):
            lines.append(f"| {value} | {d_dev[value]} | {d_test[value]} |")
        lines.append("")
    tricky = Counter(r.label.tricky_type.value for r in [*dev, *test] if r.label.tricky_type)
    lines += [
        "**tricky_type** (both splits): "
        + ", ".join(f"{k} {v}" for k, v in sorted(tricky.items())),
        "",
    ]

    status = "**partial**" if report.paused or len(done) < len(report.sample_ids) else "complete"
    lines += [
        "## Blind validation",
        "",
        f"Stratified sample of {len(report.sample_ids)} tickets (by category x difficulty, across both splits). Status: {status}, {len(done)} labeled"
        + (f", {len(failed)} failed on every validator model" if failed else "")
        + (
            ", stopped by rate limits; rerun `make validate-data` to continue"
            if report.paused
            else ""
        )
        + ".",
        "",
        "Labeled by: " + (", ".join(f"`{m}` {n}" for m, n in by_model.items()) or "none yet"),
        "",
        "| field | Cohen's kappa (spec vs validator) | disagreements |",
        "|---|---|---|",
    ]
    per_field = Counter(f for q in queue for f in q["disagreements"])  # type: ignore[attr-defined]
    for f in FIELDS:
        k = kappa.get(f)
        lines.append(f"| {f} | {'n/a' if k is None else f'{k:.3f}'} | {per_field[f]} |")
    lines += [
        "",
        f"`review_queue.jsonl` holds {len(queue)} tickets with at least one disagreement, all `open`. "
        "A disagreement means the text may not carry its spec label; a human decides. Labels are never changed silently.",
        "",
        "## Files (SHA-256)",
        "",
        "| file | sha256 |",
        "|---|---|",
    ]
    lines += [f"| `{name}` | `{h}` |" for name, h in hashes.items()]
    lines += [
        "",
        "`TEST_SET_LOCK` pins `tickets_test.jsonl`; `tests/test_dataset_lock.py` fails if the file changes. "
        "Prompt and agent work tune on dev only.",
        "",
    ]
    return "\n".join(lines)


def run_validation(settings: Settings, llm: LLMClient | None = None) -> int:
    data = settings.data_dir
    report = Report()
    rows, report.schema_errors = load_rows(data / "generated.jsonl")
    report.rows_in = len(rows) + len(report.schema_errors)
    rows, report.duplicates = dedup(rows, settings.dedup_threshold)
    by_id = {r.ticket.ticket_id: r for r in rows}

    dev, test = split(rows, settings.seed)
    write_jsonl(data / "tickets_dev.jsonl", dev)
    write_jsonl(data / "tickets_test.jsonl", test)
    test_ids = {r.ticket.ticket_id for r in test}

    sample = stratified_pick(
        sorted(rows, key=lambda r: r.ticket.ticket_id),
        VALIDATOR_SAMPLE_N,
        _stratum,
        settings.seed + 1,
    )
    sample.sort(key=lambda r: r.ticket.ticket_id)
    report.sample_ids = [r.ticket.ticket_id for r in sample]
    report.validated, report.paused = asyncio.run(
        validate_all(sample, llm or LLMClient(settings), settings)
    )

    queue = review_entries(by_id, report.validated, test_ids)
    write_jsonl(data / "review_queue.jsonl", queue)
    kappa = kappas(by_id, report.validated)

    lock_hash = sha256_file(data / "tickets_test.jsonl")
    (data / "TEST_SET_LOCK").write_text(
        f"{lock_hash}  tickets_test.jsonl\n", encoding="utf-8", newline="\n"
    )
    hashes = {
        name: sha256_file(data / name)
        for name in (
            "generated.jsonl",
            "tickets_dev.jsonl",
            "tickets_test.jsonl",
            "review_queue.jsonl",
        )
    }
    card = render_card(report, dev, test, queue, kappa, hashes, settings)
    (data / "dataset_card.md").write_text(card, encoding="utf-8", newline="\n")

    done = sum(1 for v in report.validated if v.label is not None)
    print(
        f"rows={report.rows_in} schema_errors={len(report.schema_errors)} duplicates_dropped={len(report.duplicates)} dev={len(dev)} test={len(test)}"
    )
    print(
        f"validator: {done}/{len(sample)} labeled"
        + (" (PAUSED: rate-limited, rerun to continue)" if report.paused else "")
    )
    print(f"kappa: {kappa}")
    counts = Counter(f for q in queue for f in q["disagreements"])  # type: ignore[attr-defined]
    print(f"review queue: {len(queue)} tickets; by disagreement type: {dict(counts)}")
    print(f"TEST_SET_LOCK {lock_hash}")
    return 0 if not report.schema_errors else 1
