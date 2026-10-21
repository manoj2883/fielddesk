# FieldDesk

Triage agent for dealer → manufacturer support tickets. The OEM is fictional (Ridgeline Equipment). Python 3.12, uv, LangGraph 1.2, FastAPI.

## Commands

```bash
uv sync                    # install (lockfile committed)
make lint                  # ruff check + ruff format --check + mypy (strict on schemas.py)
make test                  # pytest with coverage; network blocked except loopback
RUN_LIVE=1 make test-live  # 3 real API calls on dev tickets (costs money)
make gen-sample            # 10 generated tickets for review (costs money)
make gen-data              # full 200-ticket generation (cached by spec hash; reruns are free)
make validate-data         # dedup + blind second-model labeling + review queue + split
make baseline              # keyword baseline + agent on the DEV split only
make serve                 # uvicorn on :8000
```

On Windows, if `uv` or `make` isn't found, open a new shell (winget updated PATH).

## Layout

- `src/fielddesk/`: schemas, config, llm (spend ledger), guards, graph + `nodes/`, prompts, api, store, judge (Chapter 2 seam)
- `data/`: `LABELING_GUIDE.md`, `domain/` fixture, `generator/` pipeline, dataset JSONL files, `dataset_card.md`
- `evals/`: `baseline.py`, `results/` (one JSON per run, plus per-ticket JSONL)
- `docs/`: `DECISIONS.md` (ADR-lite), `JOURNAL.md` (what broke and the fix)
- `plans/chapter-1.md`: the plan and its Progress table

## Hard rules

- **Fictional data only.** No real OEMs, dealers, products, part numbers, people, phones or emails.
- **Labels come from the spec** (`fielddesk.labeling`), never from an LLM. Disagreements go to `data/review_queue.jsonl`, never a silent relabel.
- **Never read `data/tickets_test.jsonl`** during prompt or agent work. Only its hash (`data/TEST_SET_LOCK`) is checked. Tune on dev.
- **Spend cap:** `FIELDDESK_MAX_SPEND_USD` (default $5). The ledger at `.spend/ledger.jsonl` is cumulative across runs. Don't delete it to get around the cap.
- **Secrets** live only in `.env`. Never print or log keys. Only `.env.example` is committed.
- **No temperature** parameter: current Claude models reject non-default values.
- One retry layer: SDK `max_retries=0`, and LangGraph `RetryPolicy(retry_on=is_transient)`.
- A failed LLM call must end in a valid escalated `TriageResult` (`llm_error`), never a 500.
- Run `make lint test` after every change and show the output before claiming it passes.
- Record real decisions in `docs/DECISIONS.md` and real failures in `docs/JOURNAL.md`.
- Commits use conventional prefixes: `feat:`, `fix:`, `test:`, `data:`, `docs:`, `chore:`.
- Chapters 2–4 (LLM judge, stress tests, CI gate, circuit breaker) are out of scope until started.
