"""S6 dataset pipeline: dedup, stratified sampling and split, blind validator (mock transport),
review queue, kappa, lock and card."""

import json
import random
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
import pytest

from data.generator import generate as gen
from data.generator import validate as val
from data.generator.generate import to_ticket
from data.generator.spec import build_specs
from fielddesk.config import Settings
from fielddesk.llm import LLMClient
from fielddesk.schemas import Category, LabeledTicket
from tests.test_openrouter import completion

SPECS = build_specs()
PRIMARY = "google/gemma-4-31b-it:free"
FALLBACK = "dots-studio/dots-3-note-preview:free"


VOCAB = [f"w{n}" for n in range(2000)]


def row(i: int, message: str | None = None) -> LabeledTicket:
    """Synthetic text: 25 seeded random words, so no two rows are near-duplicates."""
    spec = SPECS[i]
    words = random.Random(i).sample(VOCAB, 25)
    text = gen.GeneratedText(subject=f"Ticket {i}", message=message or " ".join(words))
    return LabeledTicket(ticket=to_ticket(spec, text), label=spec.label, spec_hash=spec.spec_hash)


ROWS = [row(i) for i in range(len(SPECS))]


def agreeing(r: LabeledTicket, **override: Any) -> dict[str, Any]:
    out = {f: getattr(r.label, f) for f in val.FIELDS} | {"rationale": "facts"}
    return json.loads(json.dumps(out | override, default=str))


def settings(tmp_path: Path) -> Settings:
    return Settings(
        openrouter_api_key="test-or-key",
        anthropic_api_key=None,
        ledger_path=tmp_path / "ledger.jsonl",
        openrouter_min_interval_s=0.0,
        validator_model=PRIMARY,
        validator_fallback_model=FALLBACK,
        data_dir=tmp_path / "data",
    )


def client(s: Settings, handler: Any) -> LLMClient:
    return LLMClient(s, openrouter_transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(val, "CACHE_DIR", tmp_path / "cache")

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(gen.asyncio, "sleep", no_sleep)


def test_dedup_keeps_the_first_of_a_near_duplicate_pair() -> None:
    a = row(3, "The loader boom drifts down overnight, need the hold valve seal kit.")
    b = row(1, "Loader boom drifts down overnight, need the hold valve seal kit!")
    c = row(2, "Invoice shows freight charged twice on one order.")
    kept, dropped = val.dedup([a, b, c], threshold=90)
    assert kept == [b, c]  # ticket order, not input order
    assert dropped[0][:2] == (a.ticket.ticket_id, b.ticket.ticket_id)
    assert dropped[0][2] >= 90


def test_split_is_150_50_stratified_and_order_independent() -> None:
    dev, test = val.split(ROWS, seed=1729)
    assert (len(dev), len(test)) == (150, 50)
    assert not {r.ticket.ticket_id for r in dev} & {r.ticket.ticket_id for r in test}
    again = val.split(list(reversed(ROWS)), seed=1729)
    assert [r.ticket.ticket_id for r in again[1]] == [r.ticket.ticket_id for r in test]
    cats_all = Counter(r.label.category for r in ROWS)
    cats_test = Counter(r.label.category for r in test)
    for c in Category:
        assert abs(cats_test[c] - cats_all[c] / 4) <= 1.5
    assert sum(r.label.safety_risk for r in test) >= 3
    assert any(r.label.tricky_type for r in test)


def test_validator_sample_is_proportional() -> None:
    pick = val.stratified_pick(ROWS, 50, val._stratum, seed=1730)
    assert len(pick) == 50
    assert len({r.ticket.ticket_id for r in pick}) == 50
    diff = Counter(r.label.difficulty.value for r in pick)
    for level, share in (("clean", 35), ("messy", 10), ("tricky", 5)):
        assert abs(diff[level] - share) <= 1  # joint category x difficulty strata round per cell


def test_blind_view_hides_label_and_id() -> None:
    view = val.blind_view(ROWS[0])
    assert ROWS[0].ticket.ticket_id not in view
    for word in ("needs_human", "safety_risk", "difficulty", "spec_hash", "category"):
        assert word not in view
    assert ROWS[0].ticket.message in view
    assert "{guide}" not in val.system_prompt()
    assert "## 2. Rules" in val.system_prompt()


async def test_fallback_on_hard_failure_then_cache(tmp_path: Path) -> None:
    r = ROWS[5]
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        seen.append(model)
        if model == PRIMARY:
            return httpx.Response(404, text="model retired")
        return httpx.Response(200, json=completion(json.dumps(agreeing(r))))

    s = settings(tmp_path)
    out = await val.validate_one(r, client(s, handler), s)
    assert out.model == FALLBACK
    assert out.label is not None
    assert seen == [PRIMARY, FALLBACK]

    again = await val.validate_one(r, client(s, handler), s)  # cached: no new requests
    assert again.label == out.label
    assert len(seen) == 2


async def test_rate_limit_on_every_model_pauses_the_run(tmp_path: Path) -> None:
    s = settings(tmp_path)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, text="daily cap")

    done, paused = await val.validate_all(ROWS[:3], client(s, handler), s)
    assert paused
    assert done == []
    assert calls == 8  # 4 tries x 2 models on the first ticket, then the run stops


async def test_hard_failure_on_every_model_records_and_continues(tmp_path: Path) -> None:
    s = settings(tmp_path)
    done, paused = await val.validate_all(
        ROWS[:2], client(s, lambda req: httpx.Response(400, text="bad")), s
    )
    assert not paused
    assert [v.label for v in done] == [None, None]
    assert all(v.error for v in done)


def test_review_queue_and_kappa() -> None:
    rows = {r.ticket.ticket_id: r for r in ROWS[:40]}
    validated = []
    for i, r in enumerate(ROWS[:40]):
        override = (
            {"priority": "low" if r.label.priority.value != "low" else "high"} if i < 4 else {}
        )
        validated.append(
            val.Validated(
                r.ticket.ticket_id,
                val.ValidatorLabel.model_validate(agreeing(r, **override)),
                PRIMARY,
            )
        )
    ids = [r.ticket.ticket_id for r in ROWS[:4]]
    queue = val.review_entries(rows, validated, test_ids={ids[1]})
    assert [q["ticket_id"] for q in queue] == ids
    assert all(q["disagreements"] == ["priority"] for q in queue)
    assert queue[1]["split"] == "test"
    assert queue[0]["status"] == "open"
    kappa = val.kappas(rows, validated)
    assert kappa["category"] == 1.0
    assert kappa["priority"] is not None
    assert kappa["priority"] < 1.0


def test_run_validation_end_to_end(tmp_path: Path) -> None:
    s = settings(tmp_path)
    data = s.data_dir
    dup = row(199, ROWS[0].ticket.message)  # exact copy of the first row's message
    gen.write_jsonl(data / "generated.jsonl", [*ROWS[:199], dup])
    with (data / "generated.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
        fh.write('{"not": "a row"}\n')

    by_message = {r.ticket.message: r for r in ROWS}

    def handler(request: httpx.Request) -> httpx.Response:
        user = json.loads(request.content)["messages"][1]["content"]
        r = by_message[json.loads(user)["message"]]
        return httpx.Response(200, json=completion(json.dumps(agreeing(r))))

    code = val.run_validation(s, llm=client(s, handler))
    assert code == 1  # the schema error is reported, not hidden
    dev = (data / "tickets_dev.jsonl").read_text(encoding="utf-8").splitlines()
    test = (data / "tickets_test.jsonl").read_bytes()
    assert len(dev) + test.count(b"\n") == 199
    assert b"\r\n" not in test
    lock = (data / "TEST_SET_LOCK").read_text(encoding="utf-8").split()[0]
    assert lock == val.sha256_file(data / "tickets_test.jsonl")
    assert (data / "review_queue.jsonl").read_text(encoding="utf-8") == ""
    card = (data / "dataset_card.md").read_text(encoding="utf-8")
    assert "| schema errors | 1 |" in card
    assert "near-duplicates dropped" in card
    assert f"{dup.ticket.ticket_id} (of {ROWS[0].ticket.ticket_id}" in card
    assert "Status: complete, 50 labeled" in card
    assert lock in card

    # Rerun: everything cached, identical test file, so the lock still holds.
    val.run_validation(s, llm=client(s, lambda req: httpx.Response(500)))
    assert (data / "tickets_test.jsonl").read_bytes() == test
