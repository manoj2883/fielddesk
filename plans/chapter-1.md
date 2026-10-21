# FieldDesk Chapter 1: plan

Status: **approved 2026-10-20**. Change at approval: the blind validator pass runs on a **stratified sample of 50** of the 200 tickets (fits the OpenRouter free daily cap); the card reports it as partial, and the remaining 150 can be validated later by rerunning the same resumable command. The spec is the pasted "FieldDesk Chapter 1 Prompt (v2)".

## Verified facts this plan rests on (2026-10-20)

| Fact | Source |
|---|---|
| `langgraph` latest is 1.2.16 (2026-10-16). Pin `>=1.2,<1.3`. | PyPI / GitHub releases |
| `add_node(..., retry_policy=RetryPolicy(...), timeout=TimeoutPolicy(run_timeout=..., idle_timeout=...), error_handler=fn)`. Imports: `langgraph.types.RetryPolicy`, `TimeoutPolicy` and `langgraph.errors.NodeError`, `NodeTimeoutError`. The handler is `(state, error: NodeError) -> Command(update=..., goto=...)` and runs after retries are exhausted. `StateGraph.set_node_defaults(retry_policy=, error_handler=, timeout=)` is chainable; per-node arguments override it. A sync node with a timeout fails at compile time. | docs.langchain.com fault-tolerance page |
| `default_retry_on` retries almost everything, including `NodeTimeoutError`, so FieldDesk needs a custom `retry_on`. | same |
| Model IDs: `claude-haiku-5-5` ($0.10/$0.50 per MTok), `claude-sonnet-5-5` ($2/$10, cache read $0.20), `claude-opus-5-5` ($4/$20, cache read $0.20). | claude-api skill model table (cached 2026-10-06) |
| **Sonnet 5.5 and Haiku 5.5 reject a non-default `temperature` with a 400. Opus 5.5 rejects sampling parameters entirely.** | same |
| Structured output: `client.messages.parse(output_format=PydanticModel)` → `.parsed_output`. Minimum cacheable prefix is 512 tokens on the 5.5 models. | claude-api skill, Python docs |
| Machine: Windows 11, 8 GB RAM. Python 3.12 is installed. Missing: **uv, make, docker**. `ANTHROPIC_API_KEY` is not set and `ant` is not installed. | local probe |

## Decision log

| # | Decision | Why |
|---|---|---|
| D1 | classify = `claude-haiku-5-5` (effort `low`). draft = `claude-sonnet-5-5` (thinking off via `between_tools`, effort `medium`). | Classification is short-output and high-volume. The draft is the one customer-facing artifact. |
| D2 | Generator = `claude-sonnet-5-5`. Blind validator = a **free OpenRouter model**, `nvidia/nemotron-3-super-120b-a12b:free` (supports structured outputs), called through `httpx` against OpenRouter's OpenAI-compatible endpoint. The validator is isolated in `data/generator/validator.py` so the model can be swapped. Results are cached per ticket and the run is resumable, so the free tier's daily cap only delays it. | The validator then comes from a different model family than both the generator and the agent's classifier. That is a stronger independence claim than a second Claude model, at $0. Free-tier rate and daily limits are confirmed on OpenRouter's limits page before S6. |
| D10 | Docker: write the multi-stage Dockerfile but don't install Docker Desktop. Mark it "not built locally" in the README. The app runs natively with `uv run`. | Docker Desktop needs WSL2 and takes 2–4 GB of the 8 GB. Nothing else in Chapter 1 needs it. No local models run; all inference is remote, so 8 GB is enough. |
| D11 | Install `uv` (astral-sh.uv) and `make` (ezwinports.make) with winget at user level. | Approved at checkpoint 1. |
| D3 | Turn SDK retries off (`max_retries=0`). LangGraph `RetryPolicy` is the only retry layer. `retry_on` covers `RateLimitError`, `InternalServerError` / 5xx incl. 529, `APITimeoutError` and `APIConnectionError` only. | Two stacked retry layers multiply wall-clock time and hide attempt counts. A 400 must never be retried. |
| D4 | Escalation is the OR of the LLM safety flag and the deterministic guard. | A safety miss costs far more than an extra human review. |
| D5 | Labels come from spec attributes through `labeling.py` rules. The spec carries impact facts (machine_down / degraded / running, safety code, time-sensitive), and **priority is derived from them**, not picked directly. | Makes the spec → label test meaningful and the labels defensible. |
| D6 | No temperature anywhere (approved). Text diversity comes from seeded randomized surface details (symptom phrasing pool, hours, length target, typo rate, rotating seed examples) plus near-duplicate rejection. | The API rejects non-default temperature on current models. |
| D7 | The budget is a persistent cumulative ledger (`.spend/ledger.jsonl`, gitignored) across all commands. Pre-flight check: `spent + worst_case(max_tokens) > MAX_SPEND_USD` → `BudgetExceeded` before the call is made. | "Aborts before exceeding" has to hold for the whole chapter, not just one process. |
| D8 | Draft guard fails on: any `[A-Z]{2,}-?[A-Z]?\d{3,}`-style part/error code, `$` or price pattern, or timeline promise not in the ticket input; and on commitment verbs ("approved", "covered", "we will replace"). | Deterministic and testable. Errs toward escalation. |
| D9 | Metrics use scikit-learn + numpy. Near-dup detection uses rapidfuzz `token_set_ratio` ≥ 90 (configurable). Tests block the network with `pytest-socket`. | Standard and auditable. No hand-rolled F1 or kappa. |

## Steps (in order, each ends with `make lint && make test` shown)

| ID | Step | Size | Verification | Blocked by |
|---|---|---|---|---|
| S0 | Scaffold: uv project (py3.12), ruff, mypy, pytest-cov, pytest-socket, pre-commit, Makefile, `.env.example`, `.gitignore`, CLAUDE.md. **Spike:** `inspect.signature` on langgraph 1.2.16 `add_node` / `set_node_defaults` and anthropic `AsyncAnthropic.messages.parse`, recorded in JOURNAL. | S | `make lint test` green on an empty test; spike output pasted | — |
| S1 | `schemas.py` (strict), `config.py`, `labeling.py` (rule table as code) + tests; `data/LABELING_GUIDE.md`; `data/domain/*.yaml` (8 machines, 25 codes incl. 5–6 safety, 15 dealer voices) + a fixture-consistency test. | M | table-driven label tests; **CHECKPOINT 2** | S0 |
| S2 | `llm.py` (async client, structured parse, cache_control on system, usage → cost, ledger, JSON log per call) + `guards.py` (safety terms, injection markers, draft grounding) + tests on a fake client. | M | unit tests incl. budget abort | S1 |
| S3 | `nodes/` (validate, classify, route, draft_reply, check_draft, escalate), `graph.py` with `set_node_defaults` + per-node overrides, `prompts/classify_v1.md`, `draft_v1.md`, `judge.py` stub. Tests: route table (every escalation path), simulated 529 + timeout → `llm_error` result, full graph with fake LLM. | M | graph tests green | S2 |
| S4 | `store.py` (SQLite, aiosqlite), `api.py` (/triage, /runs, /health, /ready, request-id, error JSON), Dockerfile (multi-stage, uv). | S | TestClient tests; Docker *(see Q4)* | S3 |
| S5 | `data/generator/`: spec matrix (seeded, stratified mix 70/20/10, `other` ≈ 8 %, safety ≈ 10 %), versioned gen prompts + hand-written seeds, disk cache by spec hash, leakage filter, `make gen-sample` → 10 tickets. | M | **CHECKPOINT 3**: 10 tickets with spec labels + projected full-run cost | S1, S2 |
| S6 | Full generation (200) → Pydantic validation → dedup → blind validator pass → `review_queue.jsonl` → stratified split 150/50 → `TEST_SET_LOCK` + lock test → `dataset_card.md` (kappa, distributions, SHA-256). | M | `make validate-data`; review-queue counts by disagreement type printed | S5 + OK |
| S7 | `evals/baseline.py`: keyword/majority baseline + agent on **dev only**. Accuracy, macro-F1, per-class P/R, confusion, escalation and safety recall/precision, slices, bootstrap 95 % CI, ECE + reliability table, threshold sweep 0.5–0.9, guard pass rate, tokens/cache/cost/p50/p95. Results JSON + per-ticket JSONL. Opt-in live test (3 dev tickets). | M | **CHECKPOINT 4** before the agent run on dev | S3, S6 |
| S8 | Run baseline, README (Mermaid, tables, top-5 error analysis, next chapters), finalize DECISIONS / JOURNAL, 5-line summary. | S | final `make lint test` + coverage ≥ 85 % shown | S7 + OK |

Commits: one or more conventional commits per step (`chore:`, `feat:`, `test:`, `data:`, `docs:`).

## Cost estimate (whole chapter)

| Item | Calls | Est. |
|---|---|---|
| Generation, Sonnet 5.5, thinking off, cached system+seeds | ~260 (200 + regenerations + 10 sample) | $1.0–2.0 |
| Blind validation, OpenRouter free model | 200 | $0 |
| Agent baseline on dev (Haiku classify + Sonnet draft on ~110 non-escalated) | ~3 runs × 150 | ~$1.4 |
| Live tests | a few | <$0.10 |
| **Total (Anthropic)** | | **~$2.5–3.5, under the $5 cap** |

## Always / Never

- **Always:** fictional Ridgeline data only. Labels from spec only. A failed LLM call ends in a valid escalated `TriageResult`. Show command output before claiming green.
- **Never:** open `tickets_test.jsonl` during prompt work (the splitter writes it; only its hash is read). Build Chapter 2–4 features. Log or print keys. Push to a remote without being asked.

## User actions needed (not blocking S0–S4, which are fully mocked)

- Before **checkpoint 3**: put `ANTHROPIC_API_KEY` in `projects/fielddesk/.env`.
- Before **S6**: create an OpenRouter key at openrouter.ai/keys and add it as `OPENROUTER_API_KEY` (no credits; validator is `nvidia/nemotron-3-super-120b-a12b:free`, resumable over several days if the daily cap bites).

## Not yet specified (fog)

- Exact Haiku 5.5 cache-read price is not in the cached table. It will be taken from the live pricing page at S2 and recorded in config with its source.
- Whether Haiku's self-reported confidence is calibrated at all is unknown until S8. The threshold choice is made from the sweep, not in advance.

## Progress

| Step | State |
|---|---|
| S0–S8 | not started |
