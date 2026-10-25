"""Dataset pipeline entry point.

python -m data.generator.cli sample --n 10     # checkpoint 3 preview + full-run cost estimate
python -m data.generator.cli generate          # all 200 specs (cached)
python -m data.generator.cli validate          # dedup, blind validator, review queue, split (S6)
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter

from data.generator.generate import GenOutcome, generate_all, write_jsonl
from data.generator.spec import ScenarioSpec, build_specs
from fielddesk.config import get_settings
from fielddesk.schemas import Difficulty, TrickyType

DATA = get_settings().data_dir


def pick_sample(specs: list[ScenarioSpec], n: int) -> list[ScenarioSpec]:
    """A mixed preview: tricky first (injection, warranty-in-fault), then messy, then clean,
    covering as many categories as possible and at least one safety ticket."""
    wanted_tricky = [TrickyType.PROMPT_INJECTION, TrickyType.WARRANTY_IN_FAULT]
    chosen: list[ScenarioSpec] = []
    for t in wanted_tricky:
        chosen.append(next(s for s in specs if s.facts.tricky_type is t))
    seen = {s.label.category for s in chosen}
    for difficulty, k in ((Difficulty.MESSY, 3), (Difficulty.CLEAN, n - len(chosen) - 3)):
        pool = [s for s in specs if s.facts.difficulty is difficulty and s not in chosen]
        pool.sort(key=lambda s: (s.label.category in seen, -s.label.safety_risk, s.index))
        for s in pool[:k]:
            chosen.append(s)
            seen.add(s.label.category)
    if not any(s.label.safety_risk for s in chosen):
        chosen[-1] = next(
            s for s in specs if s.label.safety_risk and s.facts.difficulty is Difficulty.CLEAN
        )
    return chosen[:n]


def show(outcomes: list[GenOutcome]) -> None:
    for o in outcomes:
        lab = o.spec.label
        tricky = f"/{lab.tricky_type.value}" if lab.tricky_type else ""
        print("=" * 100)
        print(
            f"{o.spec.ticket_id}  category={lab.category.value}  priority={lab.priority.value}  "
            f"needs_human={lab.needs_human}  safety_risk={lab.safety_risk}  "
            f"difficulty={lab.difficulty.value}{tricky}"
        )
        if o.row is None:
            print(f"  REJECTED after {o.attempts} attempts: {o.rejections}")
            continue
        t = o.row.ticket
        print(
            f"  dealer={t.dealer_name} | {t.machine_model} | code={t.error_code} | "
            f"warranty={t.warranty_status.value} | hours={t.machine_hours} | attempts={o.attempts}"
        )
        print(f"  SUBJECT: {t.subject}")
        for line in t.message.splitlines():
            print(f"  | {line}")


def summarize(outcomes: list[GenOutcome], full_n: int = 200) -> None:
    cost = sum(o.cost_usd for o in outcomes)
    calls = sum(o.attempts - o.cache_hits for o in outcomes)
    rejected = sum(1 for o in outcomes if o.row is None and not o.paused)
    retries = sum(o.attempts - 1 for o in outcomes)
    hits = sum(o.cache_hits for o in outcomes)
    print("=" * 100)
    print(
        f"tickets={len(outcomes)}  api_calls={calls}  cache_hits={hits}  "
        f"leakage_retries={retries}  rejected={rejected}  spend=${cost:.4f}"
    )
    if calls:
        per_call = cost / calls
        per_ticket = per_call * (sum(o.attempts for o in outcomes) / len(outcomes))
        print(f"cost per call ${per_call:.5f}; per accepted ticket ${per_ticket:.5f}")
        print(f"ESTIMATED full generation ({full_n} tickets): ${per_ticket * full_n:.2f}")
    reasons = Counter(r for o in outcomes for rej in o.rejections for r in rej)
    if reasons:
        print(f"rejection reasons: {dict(reasons)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="data.generator.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sample = sub.add_parser("sample")
    sample.add_argument("--n", type=int, default=10)
    sub.add_parser("generate")
    sub.add_parser("validate")
    args = parser.parse_args(argv)
    settings = get_settings()
    specs = build_specs(seed=settings.seed)

    if args.cmd == "sample":
        outcomes = asyncio.run(generate_all(pick_sample(specs, args.n), settings))
        show(outcomes)
        summarize(outcomes)
        rows = [o.row for o in outcomes if o.row]
        write_jsonl(DATA / "generator" / "sample_preview.jsonl", rows)
        return 0
    if args.cmd == "generate":
        outcomes = asyncio.run(generate_all(specs, settings))
        summarize(outcomes)
        rows = [o.row for o in outcomes if o.row]
        write_jsonl(DATA / "generated.jsonl", rows)
        pending = [o.spec.ticket_id for o in outcomes if o.paused]
        failed = [o.spec.ticket_id for o in outcomes if o.row is None and not o.paused]
        print(f"wrote {len(rows)} rows to data/generated.jsonl; unresolved: {failed}")
        if pending:
            print(
                f"PAUSED by rate limits with {len(pending)} specs pending. "
                "Rerun `make gen-data` after the daily cap resets; finished tickets are cached."
            )
            return 3
        return 0 if not failed else 1
    if args.cmd == "validate":
        from data.generator.validate import run_validation

        return run_validation(settings)
    return 2


if __name__ == "__main__":
    sys.exit(main())
