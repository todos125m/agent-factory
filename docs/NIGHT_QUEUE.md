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
- [ ] 4. OpenAI provider adapter (owner decision d7: multi-provider) using the official `openai`
      SDK in its own file; key from OPENAI_API_KEY; usage mapping; mocked tests.
- [ ] 5. Update README status + a short Persian morning summary at the top of this file
      using the 9-part final report from docs/ENGINEERING_STANDARD.md (what was actually verified, failures found, remaining risks, production readiness).
