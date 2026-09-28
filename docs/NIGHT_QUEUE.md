# Night queue — autonomous work the owner already approved

A scheduled supervisor session takes the FIRST unchecked item whose prerequisites are done,
completes it, checks it off here, and pushes. One item per run. Never start anything not listed.
Rules: follow docs/ENGINEERING_STANDARD.md (mandatory: evidence-based done, adversarial pass, security review) and CLAUDE.md (skills playbook, token discipline); tests with FakeProvider only (no real
model calls); `git pull --rebase` before starting and before pushing; branch
`claude/eager-pascal-xpx6wk`; `/code-review` your diff before pushing; keep each item small.

- [x] 1. Wait for session "AF ۶" work to land: README status shows "4 — First Agents" as done.
      (Supervisor: if not yet, do nothing this run.)
      Verified 2026-09-28: README.md line 25 shows Phase 4 ✅ (researcher/customer/strategy/product
      registered + validated against docs/AGENT_BLUEPRINT.md, `/tasks/{id}/run` implemented); full
      suite green (105/105, `pytest -q`, evidence in commit 32bf165). Prerequisite for item 2 is met.
- [x] 2. Benchmark tab «سنجش» (owner decision d12): pick a fixed goal, run the manager plan step on
      2–3 route configs (provider/model/effort) via the gateway, store tokens, cost, duration,
      schema-valid yes/no and an owner 1–5 rating; panel tab to run and compare. Tests with FakeProvider.
      Done 2026-09-28: `app/benchmark.py` (fixed `BENCHMARK_GOAL`, per-route `gateway.call(route=...)`
      override added to `Gateway.call`), `POST/GET /benchmarks`, `POST /benchmarks/{id}/rate`; web tab
      «سنجش» to configure 1-5 routes, run, and compare with a rating dropdown per row. `/code-review`
      (high) on the diff found and fixed 3 real issues before push: benchmark calls had no budget
      enforcement (added a `BENCHMARK_BUDGET_USD` cap since they pass no project/task), the per-route
      try/except only caught `ProviderError`/`BudgetExceeded` so a raw SDK exception would have aborted
      every other route in the batch (broadened deliberately, still scoped to one route), and the
      original design re-queried "the last `ModelCall` row" for cost/duration which races under
      concurrent requests (fixed by having `Gateway.call` return `cost_usd`/`duration_ms` on its
      response instead). Verified: `pytest -q` 112/112 green (incl. 7 new adversarial cases: malformed
      output, provider error, raw exception, budget cap, rating validation, route-count limits); a real
      end-to-end run against a fresh sqlite file (not just the test session) confirmed the new table
      migrates and the full request/response round-trips. No real model calls (FakeProvider only).
- [x] 3. Local model provider via Ollama (free, no account; Qwen/Llama/etc.): provider "ollama"
      calling the local Ollama HTTP API (base URL from env OLLAMA_BASE_URL, default
      http://localhost:11434); JSON output parsing like the other providers; add to model_access
      choices and the settings UI; optional `ollama` service in docker-compose (profile, not default).
      Verify API shapes from official Ollama docs; mock HTTP in tests.
      Done 2026-09-28: `app/gateway/providers.py::OllamaProvider` (POST `/api/chat`, `stream:false`,
      `options.num_predict` from `max_output_tokens`, schema via `format`, `done_reason:"length"` ->
      `stop_reason:"max_tokens"`); request/response shapes verified against docs.ollama.com and the
      `ollama/ollama` Docker image (WebFetch, not memory) before coding. Registered in
      `default_providers()`; `model_access: "ollama"` in `settings_layers.py` now rewrites both
      `provider` and (unless a layer set one explicitly) `model` to a real local default
      (`llama3.2` — api_key/claude_account keep Claude IDs, untouched). Settings UI: the old binary
      switch became a 3-way select (also adding "ollama" to the per-role provider dropdown, which the
      benchmark tab's route picker reuses too). `docker-compose.yml`: optional `ollama` service behind
      a `profiles: ["ollama"]` gate, not started by plain `up`. `/code-review` (high, then a follow-up
      medium pass) on the diff found and fixed 5 real issues before push: an unhandled `ValueError` for
      an empty (not just unset) `OLLAMA_BASE_URL` — `.env.example`'s own unedited line; the owner's
      `max_output_tokens` budget being silently dropped (never sent to Ollama at all); an unhandled
      `JSONDecodeError` on a non-JSON top-level response; switching to `model_access: "ollama"` leaving
      every role's `model` on a Claude ID Ollama doesn't have; an `AttributeError` if `message` came
      back `null`; plus (2nd pass) truncated output being silently reported as a normal completion, and
      dead `.toggle`/`.switch` CSS left over from the switch->select UI change. Verified: `pytest -q`
      131/131 green (18 new adversarial provider tests: empty/blank base URL, num_predict, HTTP/URL/
      timeout/JSON errors, truncation, model-access rewrite incl. explicit-override); a real HTTP
      round-trip against a genuine local socket server (not mocked `urlopen`) confirmed the request
      body and response mapping end-to-end, twice (before and after the num_predict fix). No real
      Ollama/model calls in tests.
- [x] 4. OpenAI provider adapter (owner decision d7: multi-provider) using the official `openai`
      SDK in its own file; key from OPENAI_API_KEY; usage mapping; mocked tests.
      Done 2026-09-28: `app/gateway/openai_provider.py::OpenAIProvider` (Chat Completions API,
      `max_completion_tokens`, `response_format: json_schema` for structured output, `reasoning_effort`
      from `effort`); registered in `default_providers()` (local import to avoid a circular import with
      `providers.py`, which it imports from). API shape verified against openai/openai-python's own
      README/helpers.md plus current docs (WebFetch, not memory) — `max_tokens` -> `max_completion_tokens`
      and the `reasoning_effort`/Structured-Outputs details postdate older training data. `openai` added
      as a `pyproject.toml` optional extra (parallel to `anthropic`), `OPENAI_API_KEY` documented in
      `.env.example`/README. `/code-review` (high, then two follow-up medium passes) on the diff found
      and fixed 7 real issues before push: hardcoded `strict: true` on `response_format` would have
      rejected every real call, since none of this app's own schemas (PlanIn, CheckpointOut, TaskRunOut,
      ChatReplyOut — all with optional/defaulted fields) satisfy OpenAI's strict-mode requirements
      (verified directly against `ChatReplyOut.model_json_schema()`'s actual output) — switched to
      `strict: false`, consistent with how every caller already validates+tolerates the response after
      the fact; no `PRICES` entries for any OpenAI model meant `cost_usd()` silently returned $0 forever
      for a real, billed provider, defeating the budget check that Ollama/claude_account correctly skip
      only because they're genuinely free — added verified (Sept 2026) gpt-5/5.5/5-mini/5-nano prices,
      with an explicit "re-check before relying on this for real spend" caveat since pricing moves; the
      `effort` field (used for Anthropic reasoning roles already) was silently dropped instead of being
      sent as `reasoning_effort`; OpenAI's `prompt_tokens` already includes the cached subset (unlike
      Anthropic's `input_tokens`), so passing it through unadjusted double-billed cached tokens once
      `cost_usd()`'s shared formula added the cache-read term on top — fixed by subtracting the cached
      count; truncated output reported OpenAI's raw `finish_reason: "length"` instead of this codebase's
      cross-provider `stop_reason: "max_tokens"` marker (mirroring the same fix already made for Ollama);
      an empty `choices` list raised a raw `IndexError` instead of `ProviderError`; a Structured-Outputs
      safety refusal (`message.refusal`, `content: null`) fell through to a misleading "invalid JSON"
      error instead of surfacing the real refusal reason (mirroring `AnthropicProvider`'s explicit
      refusal check). Noted but deliberately not fixed (out of scope for this item, no functional bug):
      the truncation-normalize-then-skip-parse logic is now near-identical in three provider files with
      no shared helper — a `/simplify` candidate, not urgent. Also noted: the shared cache-read pricing
      ratio (0.1x, Anthropic's actual discount) is an approximation for other providers, documented in
      `pricing.py`'s own docstring rather than invented as a precise per-provider rate OpenAI's own cache
      discount has shifted between 50% and 90% by model generation within this same month. Verified:
      `pytest -q` 148/148 green (24 new provider tests: schema/effort/cache/truncation/refusal/empty-
      choices/budget-integration cases, incl. one exercising the real un-mocked "package not installed"
      path — confirmed by actually installing then uninstalling the real `openai` SDK in this venv to
      check both states, restoring the environment `pip install -e ".[dev]"` produces); the real SDK's
      `chat.completions.create` signature was checked twice (fresh install) to confirm every kwarg this
      provider sends (`max_completion_tokens`, `response_format`, `reasoning_effort`) is genuinely
      accepted. No real OpenAI API calls in tests.
- [ ] 5. Update README status + a short Persian morning summary at the top of this file
      using the 9-part final report from docs/ENGINEERING_STANDARD.md (what was actually verified, failures found, remaining risks, production readiness).
