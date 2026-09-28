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
- [ ] 2. Benchmark tab «سنجش» (owner decision d12): pick a fixed goal, run the manager plan step on
      2–3 route configs (provider/model/effort) via the gateway, store tokens, cost, duration,
      schema-valid yes/no and an owner 1–5 rating; panel tab to run and compare. Tests with FakeProvider.
- [ ] 3. Local model provider via Ollama (free, no account; Qwen/Llama/etc.): provider "ollama"
      calling the local Ollama HTTP API (base URL from env OLLAMA_BASE_URL, default
      http://localhost:11434); JSON output parsing like the other providers; add to model_access
      choices and the settings UI; optional `ollama` service in docker-compose (profile, not default).
      Verify API shapes from official Ollama docs; mock HTTP in tests.
- [ ] 4. OpenAI provider adapter (owner decision d7: multi-provider) using the official `openai`
      SDK in its own file; key from OPENAI_API_KEY; usage mapping; mocked tests.
- [ ] 5. Update README status + a short Persian morning summary at the top of this file
      using the 9-part final report from docs/ENGINEERING_STANDARD.md (what was actually verified, failures found, remaining risks, production readiness).
