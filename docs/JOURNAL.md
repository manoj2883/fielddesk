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

The account is free tier with **50 requests/day** (read from OpenRouter's `/key` endpoint). Ten requests went into finding working models:

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
