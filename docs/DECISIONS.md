# Decisions

ADR-lite. Each entry gives the context, the options, the choice and the trade-off. Numbering follows `plans/chapter-1.md`.

## D5 · Labels come from the spec, not from a model

- **Context:** The eval is only as good as its labels. If an LLM labels the tickets, the agent is graded against another model's guesses, and both make the same kinds of mistakes.
- **Options:** (a) the generator LLM labels each ticket as it writes it; (b) a human labels all 200; (c) each spec row states facts, and fixed rules compute the label.
- **Choice:** (c). The spec states facts (`asks`, `machine_impact`, safety code/symptom, deadline, warranty-policy fit, conflicting asks). `fielddesk.labeling` turns those facts into category, priority, needs_human and safety_risk, following the rule table in `data/LABELING_GUIDE.md`.
- **Trade-off:** A label is only as correct as the generated text is faithful to its spec. If the LLM drifts (writes "machine is fine" for a `down` spec), the label is wrong for that text. The blind second-model pass exists to catch that drift. Disagreements go to `review_queue.jsonl` and are never fixed silently.
- **Sub-decision:** priority is *derived* from machine impact and safety, not chosen per row. This lets a test assert the mapping, and stops the generator from writing "urgent" tickets whose facts say `low`.

## D3 · One retry layer, and only for transient errors

- **Context:** The Anthropic SDK retries by default (2 retries). LangGraph nodes can also have a `RetryPolicy`. With both on, one failing call can be tried 3 × 3 = 9 times, the attempt count is invisible to the graph, and the per-node timeout covers an unknown number of SDK retries.
- **Options:** (a) SDK retries only; (b) LangGraph retries only; (c) both.
- **Choice:** (b). The client is built with `max_retries=0`, and every LLM node gets `RetryPolicy(max_attempts=3, retry_on=is_transient)`. `is_transient` accepts 429, 5xx (including 529 `OverloadedError`), connection errors, timeouts, and LangGraph's `NodeTimeoutError`. It never accepts a 4xx, a refusal, or a truncated/unparsable response, because retrying those spends money for the same failure.
- **Trade-off:** LangGraph's backoff doesn't read the API's `retry-after` header the way the SDK does, so under heavy rate limiting it may retry too early. Acceptable at FieldDesk's volume. It's revisited in Chapter 4 (circuit breaker).

## D7 · A persistent spend ledger, checked before every call

- **Context:** The spec requires aborting *before* spend exceeds `MAX_SPEND_USD`. A per-process counter resets every time `make gen-data` or `make baseline` starts.
- **Choice:** `.spend/ledger.jsonl` (gitignored) records every billed call. Before a call, the wrapper reserves the call's *worst-case* cost (estimated input with no cache hit, plus a cache write, plus the full `max_tokens` of output) and refuses if `spent + reserved + worst_case > cap`. After the call, the real cost replaces the reservation.
- **Trade-off:** The worst-case estimate is pessimistic, so the last few cents under the cap can't be used. It's single-process: two processes running at the same time could both pass the check. Both are documented and acceptable for a $5 budget.

## D8 · A deterministic draft guard, tuned to over-fire

- **Context:** The draft model may invent part numbers, prices, timelines or warranty commitments. A commitment sent to a dealer by mistake is the costliest failure the drafting step can make.
- **Choice:** `check_draft` is a regex guard with no LLM. It fails on any code or part number, dollar amount, or timeline that doesn't appear in the ticket, and on any commitment phrase ("is covered", "claim approved", "we will replace", "free of charge"). Failing escalates with `guard_failed`.
- **Trade-off:** It will flag some harmless drafts (for example "we will replace the filter if…"). The guard pass rate is reported in the baseline so the cost of that strictness is visible. Chapter 2's LLM judge can grade drafts more finely, and this guard stays as a hard floor beneath it.

## D4 · Safety escalation is an OR of the model and the guard

- **Context:** Two independent signals can detect a safety hazard: the classifier's `safety_flag` and the deterministic keyword guard. Requiring both (AND) gives fewer escalations. Using either (OR) catches more.
- **Choice:** OR, and safety is checked before category `other` and low confidence, so it's the recorded reason whenever it applies. `escalation_detail` names which source fired, so the baseline can report each source's recall separately.
- **Trade-off:** More false escalations (for example "smoke" in "smoke test the new display"). One safety miss costs far more than a specialist spending 30 seconds dismissing a false alarm. The baseline reports escalation precision so the cost is visible, not hidden.

## D1 · Haiku classifies, Sonnet drafts

- **Context:** Classification is short, structured and high-volume. The draft is free text that a dealer may read, so quality matters more there and volume is lower (escalated tickets never get a draft).
- **Choice:** `claude-haiku-5-5` for classify, with structured output into `ClassifierOutput`, thinking disabled and effort `low`. `claude-sonnet-5-5` for drafts, with thinking off via `between_tools` and effort `medium`. No `temperature` on either: current models reject non-default values.
- **Trade-off:** Haiku's self-reported confidence may be poorly calibrated, and escalation depends on it. The baseline measures this (ECE, reliability table, threshold sweep) instead of assuming it.
- **Also:** the classifier's system prompt includes the triage policy (category definitions, priority rules). A real support team would hand that policy to any new triager, so giving it to the model isn't leakage: the labels come from spec *facts* the model never sees.

## D10 · Write the Dockerfile without building it

- **Context:** The dev machine is a Windows laptop with 8 GB RAM and no Docker. Docker Desktop needs WSL2 and would take 2–4 GB.
- **Choice:** Ship a multi-stage, uv-based Dockerfile (dependency layer separated from source, non-root user, healthcheck), and say plainly in the README and the Dockerfile that it hasn't been built locally. The app runs natively with `make serve`.
- **Trade-off:** The image is unverified until a CI runner builds it. That check belongs with the CI gate in Chapter 4.

## D6 · No temperature; diversity comes from the spec

- **Context:** The chapter spec asked for generation "at higher temperature". `claude-sonnet-5-5` returns a 400 for any non-default `temperature`.
- **Choice:** Variation is pushed into the seeded spec matrix instead: dealer voice (6 styles across 15 dealers), three length bands, messy traits (typos, vague, angry, shorthand, missing fields, run-on), deadline phrasings, safety-symptom phrasings, misleading-wording twists and injection sentences. The static system prompt (rules plus 8 hand-written example tickets across all categories and voices) is cached; only the per-ticket FACTS/STYLE block changes. Near-duplicates are removed afterwards with rapidfuzz.
- **Trade-off:** One generator family with one decoding setting can still converge on a "house style". That is a known limit in the dataset card. The blind second pass by a model from another family partly checks for it.

## D13 · Free OpenRouter models for every role (supersedes D1 and D2's model choices)

- **Context:** The chapter spec named Claude Haiku-class and Sonnet-class models. The project owner chose $0 free models over buying API credits, and noted the choice can be reversed later.
- **Options:**
  - (A) Claude through the Anthropic API, about $3 for the chapter;
  - (B) Claude through OpenRouter credits;
  - (C) free OpenRouter models for everything.
- **Choice:** (C), with the switch kept cheap. `llm.py` picks the backend per model ID: `vendor/model` goes to OpenRouter's chat-completions API, a bare `claude-*` ID goes to the Anthropic SDK. Moving back to Claude is four lines in `.env`, and both paths have tests. Defaults:
  - generator and draft: `google/gemma-4-31b-it:free`
  - classify: `minimax/minimax-m3.1-flash:free`
  - blind validator: `nvidia/nemotron-3-super-120b-a12b:free`
  The generator–validator and classifier–validator pairs each come from different families.
- **What changes in the reliability story:**
  - Structured output uses native `json_schema` where the model's endpoints support it. Otherwise it uses JSON mode with the schema in the prompt, and Pydantic validation is the real gate. Invalid JSON is a non-retryable `LLMResponseError`, so it escalates as `llm_error`. Schema-failure rate becomes a measured reliability number.
  - Transient errors are now 408/429/5xx/timeouts on either backend. OpenRouter can report upstream failures inside an HTTP 200 body, so those are converted to typed errors too.
  - A client-side pacer spaces OpenRouter calls 3.2 s apart, to stay under the free tier's per-minute limit. A daily-cap 429 can't be fixed by retrying, so the run stops cleanly and resumes from cache.
  - Prompt caching and cache-hit metrics mostly don't apply to free models. They're reported as measured (usually 0), not dropped.
- **Trade-off:** The agent being evaluated is no longer Claude. Free models can change or disappear without notice, which hurts reproducibility, so every results file records the exact model IDs and dates. Quality is unknown until the baseline measures it.
