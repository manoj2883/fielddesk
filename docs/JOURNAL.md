# Build journal

What broke, the evidence, and the fix. Newest entries go at the bottom. Failures are kept, not tidied away.

## 2026-10-20 · S0 toolchain

- **uv and make were missing** on the Windows dev box. I installed them with winget (`astral-sh.uv` 0.13.4, `ezwinports.make` 4.4.1). winget only updates PATH for new shells, so the first `uv --version` in the existing shell failed with `command not found`.
- **Docker is not installed.** The machine has 8 GB RAM, and Docker Desktop plus WSL2 would take 2–4 GB of it. The Dockerfile is written but has not been built locally (see DECISIONS D10).
- **API spike before writing the graph.** I checked signatures with `inspect.signature` against what is installed (langgraph 1.2.16, anthropic 1.17.0) instead of relying on memory:
  - `StateGraph.add_node(..., retry_policy=, cache_policy=, error_handler=, timeout: float | timedelta | TimeoutPolicy, ...)`
  - `StateGraph.set_node_defaults(*, retry_policy, cache_policy, error_handler, timeout)`
  - `TimeoutPolicy(*, run_timeout, idle_timeout, refresh_on)` and `NodeError(node, error)`
  - `AsyncAnthropic.messages.parse(...)` takes `output_format=`, `system=`, `thinking=`, `output_config=`. It has **no top-level `cache_control`**, so the cache breakpoint goes on the system text block.
  - The SDK exposes `OverloadedError` (529), `ServiceUnavailableError`, `RateLimitError`, `InternalServerError`, `APITimeoutError` and `APIConnectionError`. These are the only exceptions the graph retries.
- **Temperature is unavailable.** Sonnet 5.5 and Haiku 5.5 return a 400 for a non-default `temperature`, which the spec's "higher temperature" generation step relied on. Diversity now comes from seeded variation instead (DECISIONS D6).

## 2026-10-20 · S1 domain fixture: YAML flow-mapping comma

- **Symptom:** `test_safety_codes_cover_the_named_hazards` failed. No safety code's meaning contained "fire", although two were written that way.
- **Cause:** `{..., meaning: exhaust aftertreatment over-temperature, fire risk}` is a YAML flow mapping. The comma ended the value, and `fire risk` was parsed as a separate key with a null value. The meaning was silently cut short and nothing raised an error.
- **Fix:** every `meaning` is now quoted. The test that caught it stays. Without it, the generator would have described RX-E128 and RX-E655 without the fire hazard, which is the fact that makes them safety codes.

## 2026-10-20 · S2 network-blocking fixture vs the Windows event loop

- **Symptom:** the first async test in `test_llm.py` errored with `SocketBlockedError: A test tried to use socket.socket`, even though the fake client makes no network calls.
- **Cause:** on Windows, asyncio's proactor event loop opens an AF_INET loopback socket pair for its self-pipe. `--disable-socket --allow-unix-socket` blocks every non-Unix socket, so the event loop couldn't start.
- **Fix:** pytest now runs with `--allow-hosts=127.0.0.1,::1`. Sockets can be created, but connecting to anything other than loopback raises. The smoke test now connects to a TEST-NET-3 IP literal (`203.0.113.10`) instead of a hostname, so it also proves no DNS lookup happens before the block.

## 2026-10-20 · S2 regex miss in the brake guard

- The first draft of the brake pattern matched "brakes not holding" but not "brakes **are** not holding". The table-driven test caught it before any data existed. Fixed with an optional `(are|is)` and kept as a test case.

## 2026-10-20 · S3 the error handler is a node, so it needs the timeout rules too

- **Symptom:** `builder.compile()` raised `ValueError: Node timeouts are only supported for async nodes ... Node '__default_error_handler__' is sync.`
- **Cause:** `set_node_defaults(timeout=..., error_handler=...)` applies the default timeout to every node, and LangGraph runs the error handler as a node (`__default_error_handler__` in the compiled graph). The docs' handler example is a sync `def`. Combined with a default timeout, that example fails at compile time.
- **Fix:** `on_node_error` is `async def`. A comment in `graph.py` points back here.
- **Also:** mypy rejects the documented two-argument handler `(state, error: NodeError)`, because the stubs only model single-argument nodes. That line has a scoped `# type: ignore[arg-type]` with a comment explaining why. Node factories return a `NodeFn` Protocol, since a `Callable[[TriageState], ...]` alias doesn't match LangGraph's node protocol, which requires the parameter to be named `state`.
- **Failure-path tests** (`tests/test_graph.py`), all against a scripted fake client:
  - one 529 then success: retried, and a draft is produced;
  - persistent 529: 3 attempts, then `llm_error`;
  - 400 and refusals: one attempt each, no retry;
  - a draft call that hangs: `NodeTimeoutError`, retried once, then `llm_error`, with the classification kept;
  - budget exhausted: no API call, `llm_error`;
  - a crash outside the graph: the last-resort catch in `TriageAgent.run` still returns a valid result.

## 2026-10-21 · Provider switch to free OpenRouter models

- Added an OpenRouter backend next to the Anthropic one, without changing the `LLMClient.parse/text` interface. The nodes, generator and graph didn't need edits. Only the tests that relied on the old Claude defaults had to pin `claude-*` IDs.
- New tests run against `httpx.MockTransport`:
  - JSON-mode schema injection and fenced-JSON parsing;
  - native `json_schema` for capable models;
  - schema violations, truncation, content filter, 429, and a 200-with-error body;
  - pacer spacing;
  - the full graph on free-model IDs;
  - a 429 retried 3 times, then a clean `llm_error`.
- Still unverified until a key exists: whether these exact free models honor JSON mode reliably, and the account's real daily cap. Both are checked at checkpoint 3.

## 2026-10-21 · Live smoke tests: which free models actually work

The account is free tier (`is_free_tier: true` from OpenRouter's `/key` endpoint). *Correction, same day:* I first read `limit: 50` as the daily request cap. But `limit_remaining` stayed at 50 after about 15 requests, so that field is a credit limit, not a request counter. The real daily request cap isn't exposed by the API. The pipeline treats it as unknown, and a daily-cap 429 just pauses a resumable run. Ten requests went into finding working models:

| Model | Role tried | Result |
|---|---|---|
| `minimax/minimax-m3.1-flash:free` | classify | ✅ valid JSON in JSON mode, 2.4 s, correct category and priority |
| `nvidia/nemotron-3-super-120b-a12b:free` | validator | ❌ HTTP 404: the upstream provider's function is "not found for account" |
| `nvidia/nemotron-3.5-super-vl:free` | validator | ✅ native `json_schema`, 6.9 s, correct |
| `dots-studio/dots-3-note-preview:free` | validator | ✅ 17.3 s, correct |
| `google/gemma-4-31b-it:free` | generator | ❌ HTTP 429 "temporarily rate-limited upstream" (twice) |
| `google/gemma-4-26b-a4b-it:free` | generator | ❌ HTTP 429 upstream |
| `xingchen-agi/xing4.0-29b-a4b:free` | generator | ❌ HTTP 429 upstream |
| `thinkingmachines/inkling:free` | generator | ❌ HTTP 403 "only available on agentic harnesses" |
| `dots-studio/dots-3-note-preview:free` | generator | ❌ hit `max_tokens` at 1,500 (reasons before answering). At 4,000 the call **hung over 5 minutes**, then `httpx.ReadError` |

**Two fixes came out of this:**
1. **A hard wall-clock timeout on every OpenRouter call** (`asyncio.timeout`). httpx's timeout is per read, so a server that sends bytes slowly can hold a request open with no upper bound. The node timeout would have caught it inside the graph, but the generator runs outside the graph and had no cap. Covered by `test_hard_timeout_caps_a_trickling_server`. `TimeoutError` is transient, so it's retried.
2. **Higher `max_tokens` for free models** (classify and draft 2,048, generator 3,000). Reasoning-style models spend output tokens before answering. These models cost $0, so this has no budget effect.

**Role assignment, driven by what works and by independence:**
- generator: MiniMax
- classifier: Nemotron 3.5 VL
- draft: MiniMax
- validator: Gemma 4 31B, falling back to dots-3

Generator, classifier and validator are three different families. That matters more than which model is "best": the classifier must not grade text written by its own model, and the validator must not share a family with either.

**Lesson for the README:** with free models, "which model" is a runtime fact, not a design-time one. Results files record the model IDs actually used, and the pipeline has to tolerate a model disappearing mid-run.

## 2026-10-21 · First live sample run: four bugs before a single ticket was usable

1. **One truncated response crashed the whole batch.** `asyncio.gather` re-raised the first `LLMResponseError`. Fix: an unusable answer (truncated, refused, bad JSON) now fails only that attempt, gets recorded as `bad_response:*`, and the next attempt runs. Test: `test_bad_response_fails_one_attempt_not_the_run`.
2. **Cache files were written as NTFS alternate data streams.** The cache filename included the model ID `minimax/minimax-m3.1-flash:free`. The `/` made a subdirectory, and on Windows the `:` turns `name:free_a0.json` into a hidden stream on a file called `..._minimax`. `ls` showed 4 odd entries and no `.json` files. Fix: model IDs are reduced to `[A-Za-z0-9._-]` in filenames. Test: `test_cache_filename_is_portable`.
3. **Hidden reasoning ran past `max_tokens`.** MiniMax used 699 reasoning tokens to write a 496-character ticket, and occasionally runs past 3,000. The generator limit is now 6,000. These models are free, so there's no budget effect.
4. **Fact sheets copied word for word.** A sample ticket said "There is currently no effect on the machine's operation", which is the renderer's wording, not a dealer's. Fix: `generate_v2.md` adds rule 9, "never copy the wording of the FACTS", with examples. `generate_v1.md` stays in the repo, so the change is visible in history.

## 2026-10-21 · Sample v1 review: labels right, text wrong

All 10 tickets in the first live sample passed the leakage filter, and their labels matched their specs. They still weren't usable:
- **The prompt-injection ticket had no injection.** FD-0070 left out the required sentence, so a "tricky" row tested nothing. New fidelity check: `missing_injection`. It compares with quotes and whitespace stripped, because models restyle quotes when they copy.
- **Asks were the renderer's category definitions, echoed back.** Examples: "correct or explain an invoice, credit, statement, or payment issue", and "factory field technician visit, service campaign slot, training, booking change". Unnatural, and a gift to any keyword classifier. **Fix (spec v2):** each ask carries one concrete situation from `details.py`. Examples: "freight was charged twice on one order", "the part needed is a shift solenoid". Parts and symptoms follow the error code's subsystem, so the text stays consistent with the code. The details use their own seeded RNG stream, so they don't reshuffle any facts.
- **"The machine is operating normally" in every billing ticket.** That was my renderer stating `machine_impact: none`. Now no-impact is conveyed by leaving the line out.
- A test caught that one invented part, "steering **priority** valve", would trip the label-word filter on every ticket that used it. Renamed to "steering control valve".

## 2026-10-21 · Sample v2 review, and a label the agent could never earn

- Sample v2 reads like real tickets: concrete asks, varied voices, the injection present (after 3 `missing_injection` retries). One generation hit `max_tokens` and was retried as a bad response without stopping the run.
- **Label validity problem:** FD-0162 was labeled `safety_risk=True` because of RX-E230 (park-brake engagement failure), but the text only said "RX-E230". In Chapter 1 the agent has no code lookup, so that label is unreachable. It would score as a safety miss, but the miss would come from the dataset, not the agent. Fix: the renderer tells the generator to say in plain words what the code is about, and a new check rejects safety-code tickets with no hazard word (`safety_code_unexplained`). Chapter 2 grounding (code lookup) would make the harder version of this case fair, and it's listed as a next-chapter idea.
- **Cache key now includes a hash of the rendered instructions.** Before, a renderer change (like the one above) could have served a stale cached ticket for an unchanged spec.
- **My own tooling bug:** the patch that added the hazard regex was written through a non-raw Python string, so `\b` landed in the file as a literal backspace (0x08). The regex could never match. The new test caught it. The full test run also reported 8 h 47 m: the laptop slept mid-run. Rerun: 26 s.

## 2026-10-24 · S6 day 1: a retired model, the daily cap, and a check that never ran

- **MiniMax's `:free` variant was retired on 2026-10-22.** OpenRouter returns 404 and points to the paid ID. Generation and drafting both used it. The user chose to stay on free models over buying credits. Smoke test, one call each on FD-0070 (prompt injection, the hardest fidelity case):

  | Model | Result |
  |---|---|
  | `poolside/laguna-s-2.1:free` | ✅ 5.0 s, 147 output tokens, injection present, its own dealer voice |
  | `recursive/flint-v1.0:free` | ⚠️ 19 s, 2,842 output tokens, echoed the fact-sheet wording ("or we are asking for goodwill, or the cause may be operator damage") |
  | `xingchen-agi/xing4.0-29b-a4b:free` | ❌ HTTP 429 upstream (as on 2026-10-21) |

  Generator and draft moved to Laguna (Poolside), which is a fourth family. A new 10-ticket sample was approved at checkpoint 3: 10/10 accepted, 2 invented-code retries, $0. Known text quirks: invented reference numbers (in style for the "formal" voice), and one logic slip (FD-0007 says "received 2, billed 1" but asks for an overcharge credit).
- **The daily cap is now measured: 50 free-model requests a day** (`free-models-per-day`, `X-RateLimit-Limit: 50`). The run stopped at 54 of 200 tickets. It also stopped with a traceback, because one exhausted 429 ended `asyncio.gather`. Fix: a transient error that outlasts every retry raises `GenerationPausedError`. The run then skips every spec it hasn't started, prints "PAUSED", and exits with code 3. Test: `test_rate_limit_pauses_the_run_without_crashing`.
- **The `safety_code_unexplained` check had never run during generation.** It was added in 6624be5 with a unit test on `check_text`, but `generate_one` never passed `safety_code=`, so the default `False` switched it off. The unit test proved the function, not the pipeline. Fix plus a regression test through `generate_one`. All 4 safety-code tickets cached so far already name the hazard, so no data was affected. **Lesson:** test a filter at the call site that's supposed to apply it.
- **rapidfuzz 3 doesn't normalize by default.** `token_set_ratio("The loader … kit.", "Loader … kit!")` scored below 90, so case and punctuation could hide a duplicate. Dedup now uses `processor=utils.default_process`. A related caveat: `token_set_ratio` scores 100 whenever one message's tokens are a subset of the other's. My first synthetic test rows shared a template, and 199 of 200 were dropped. The real score distribution gets checked before the threshold is trusted.
