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
