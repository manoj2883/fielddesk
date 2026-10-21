# FieldDesk

> **Work in progress: Chapter 1 of 4.** This README will be replaced by the full write-up (architecture, baseline numbers with confidence intervals, error analysis) when Chapter 1 is finished.

FieldDesk triages support tickets that equipment dealers send to a manufacturer, and drafts a reply for the manufacturer's support team to review. A human always sends the final reply.

**The flow:** a customer's tractor, combine or loader breaks down, and they take it to their dealer. If the dealer's technicians can't fix it, or need a part or a warranty decision, dealer staff open a ticket with the manufacturer. FieldDesk works on the manufacturer's side.

The manufacturer, **Ridgeline Equipment**, is fictional. So are every machine model, error code, part number and dealer in this repo.

## Chapter 1 scope

- A LangGraph triage agent with these steps: validate, classify, route, draft, then a deterministic check on the draft, with escalation when anything is uncertain. Retries, per-node timeouts and error handlers are set up so that a failed LLM call always ends in a valid escalated result, never a crash.
- A labeled synthetic dataset of 200 tickets. Labels are **computed from the scenario specs by fixed rules**, never by an LLM ([labeling guide](data/LABELING_GUIDE.md)). A blind second pass by a model from a different family checks the generated text against those labels.
- A reproducible baseline against a no-LLM keyword baseline. It reports accuracy, macro-F1, escalation and safety recall, calibration, cost and latency.

Later chapters: an LLM judge, stress testing, a CI quality gate, and cost controls.

## Progress

| Step | Status |
|---|---|
| Repo, tooling (uv, ruff, mypy strict on schemas, pytest with network blocked) | ✅ |
| Schemas, labeling rules as tested code, Ridgeline domain fixture | ✅ |
| LLM wrapper (spend cap, cost accounting, prompt caching) and deterministic guards | ✅ |
| Agent graph (retries, timeouts, error handler), API, SQLite persistence | ✅ |
| Synthetic dataset (200 tickets) and validation | ⏳ |
| Baseline evaluation and error analysis | ⏳ |

The build story is kept as it happens: design decisions in [docs/DECISIONS.md](docs/DECISIONS.md), and failures with their fixes in [docs/JOURNAL.md](docs/JOURNAL.md).

## Run the checks

```bash
uv sync
make lint test
```
