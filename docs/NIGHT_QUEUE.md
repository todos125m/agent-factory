# Night queue — autonomous work the owner already approved

## خلاصه صبحگاهی (۲۸ سپتامبر ۲۰۲۶)

۱. **مسئله**: تکمیل فاز ۴ (اولین agentهای متخصص) که در طول شب لند شد، و سپس اجرای صف کارهای شب
   تأییدشده (این فایل) — یک آیتم در هر نوبت، هرکدام با `/code-review` قبل از push.

۲. **زمینه/تصمیم‌ها**: آیتم ۲ تصمیم owner d12 (تب سنجش)، آیتم ۳ و ۴ تصمیم d7 (گیتوی چندارائه‌دهنده).
   هیچ فراخوانی واقعی مدل انجام نشد؛ همه‌چیز با FakeProvider یا mock تست شد.

۳. **تغییرات** (۴ آیتم، ۴ commit روی `claude/eager-pascal-xpx6wk`):
   - آیتم ۱: تأیید لندشدن فاز ۴ (پژوهشگر/مشتری/استراتژی/محصول + `/tasks/{id}/run`).
   - آیتم ۲: تب «سنجش» — مقایسه‌ی provider/model/effort روی یک هدف ثابت، با توکن/هزینه/زمان/
     اعتبار schema و امتیاز مالک؛ به `Gateway.call` قابلیت override مسیر اضافه شد.
   - آیتم ۳: provider محلی و رایگان Ollama (`/api/chat`)، سوییچ سه‌حالته‌ی `model_access` در UI.
   - آیتم ۴: provider رسمی OpenAI (Chat Completions API) در فایل جداگانه‌ی خودش.

۴. **معماری**: هر سه provider جدید همان الگوی موجود (`Provider` protocol در `app/gateway/providers.py`،
   `ModelRequest`/`ModelResponse`/`Usage`) را دنبال می‌کنند؛ هیچ‌کدام مستقیم توسط agent فراخوانی
   نمی‌شوند — همه از `Gateway.call` رد می‌شوند (بودجه‌چک + لاگ `ModelCall`، طبق CLAUDE.md).

۵. **تأیید (Verification)**: تعداد تست از ۸۴ (شروع شب) به ۱۴۸ رسید — همه سبز (`pytest -q`).
   شکل درخواست/پاسخ Ollama و OpenAI از داکیومنت رسمی/SDK واقعی تأیید شد (WebFetch/WebSearch، نه
   حافظه)؛ Ollama با یک سرور HTTP واقعی (نه mock) دوبار تست شد؛ SDK واقعی OpenAI یک‌بار نصب و
   امضای `chat.completions.create` بررسی شد، سپس حذف شد تا محیط با `pip install -e ".[dev]"` یکی بماند.

۶. **اشکالات پیداشده و رفع‌شده** (توسط `/code-review`، مجموعاً ~۲۰ مورد در ۴ آیتم؛ مهم‌ترین‌ها):
   بای‌پس اعتبارسنجی interview (پاسخ خالی/None)؛ race در محاسبه‌ی هزینه‌ی سنجش (رفع با برگرداندن
   cost/duration از خود Gateway)؛ نبود سقف بودجه برای سنجش؛ `OLLAMA_BASE_URL` خالی که URL نسبی
   می‌ساخت؛ نادیده‌گرفتن سقف توکن خروجی در Ollama؛ `strict:true` در OpenAI که هر فراخوانی واقعی را رد
   می‌کرد (schemaهای خود پروژه فیلد اختیاری دارند)؛ نبود قیمت مدل‌های OpenAI که چک بودجه را بی‌اثر
   می‌کرد؛ دوبار-محاسبه‌ی هزینه‌ی توکن کش‌شده‌ی OpenAI.

۷. **ریسک‌های باقی‌مانده**: قیمت‌های OpenAI و نسبت تخفیف کش (۰.۱x، مدل Anthropic) تخمینی‌اند و باید
   قبل از تکیه‌ی مالی روی آن‌ها با داشبورد واقعی OpenAI چک شوند؛ Ollama و OpenAI هرگز با حساب/سرور
   واقعی owner تست نشده‌اند (فقط mock/HTTP-mock)؛ سرویس `ollama` در docker-compose هرگز واقعاً
   `docker compose up` نشده (فقط YAML آن اعتبارسنجی شد)؛ منطق نرمال‌سازی truncation در سه فایل
   provider تکرار شده (کاندید `/simplify`، نه باگ).

۸. **کار باقی‌مانده**: آیتم ۵ همین (این خلاصه + README) — با این commit کامل می‌شود. صف شب برای
   امشب تمام است؛ آیتم‌های آینده باید توسط owner یا یک صف جدید اضافه شوند.

۹. **آمادگی Production**: کد و تست‌ها آماده‌اند (۱۴۸/۱۴۸ سبز)، اما **COMPLETED واقعی روی زیرساخت
   واقعی owner اعلام نمی‌شود** تا زمانی‌که حداقل یک اجرای واقعی (نه mock) روی هرکدام از Ollama/OpenAI
   با کلید/سرور واقعی owner انجام و دیده شود. تا آن زمان: قابل‌استفاده در dev/test، نه برای spend واقعی
   بدون بازبینی دستی قیمت‌ها.

---

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
- [x] 5. Update README status + a short Persian morning summary at the top of this file
      using the 9-part final report from docs/ENGINEERING_STANDARD.md (what was actually verified, failures found, remaining risks, production readiness).
      Done 2026-09-28: README's Model Gateway row updated (Anthropic/claude_account/Ollama/OpenAI,
      no longer "OpenAI adapter pending"), UI shell row mentions benchmark comparison; the 9-part
      Persian morning summary above covers items 1-4 (changes, architecture, verification, failures
      found and fixed, remaining risks, production readiness). This is a docs-only change; `pytest -q`
      re-run to confirm still 148/148 green before push (evidence, not assumption).

---

## Queue 2 — from the real test run of 2026-09-28 (run on the owner's laptop session)

Same rules as above. Additionally: HARD SPEND CAP — if this work's session cost passes $10, stop,
push what is verified, and report. Real model calls are allowed only for the final verification of
each item (the laptop uses the owner's own Claude account, `model_access: claude_account`).

- [x] 6. **Project mode is ignored (bug, root cause known).** `Project.mode` (set at creation, e.g.
      "automatic") is never read: `settings_layers.resolve()` returns `DEFAULTS["mode"] =
      "manual_learning"` (app/settings_layers.py:16) unless a settings layer overrides it, so
      `manager.py:254` never auto-decides and the manager's prompt says "manual_learning" for an
      automatic project (seen in two real runs). Fix: make the project's own `mode` (and a task's
      `mode` when set) feed the resolved settings, with a clear precedence; regression tests for
      automatic → auto-decide and manual_learning → wait for the owner.
      Done 2026-09-28: `settings_layers.resolve()` folds each entity's own fields in at its scope —
      DEFAULTS ← global ← workspace ← `Project.mode`/`Project.budget` ← project layer ← `Task.mode` (if
      set) ← task layer. Decision: a settings layer at the same scope still beats the entity field. Reason:
      the same rule `Project.budget` already followed, and it keeps the Settings page's per-project/task
      mode override working. Trade-off: every project has a mode, so a global/workspace "mode" no longer
      affects any project, and the project chip (`Project.mode`) can differ from the effective mode if a
      project/task layer overrides it. Result: checkpoint auto-decide, the plan prompt's "Mode:" line and
      Learning-Trace capture all read one resolved mode. Root cause confirmed on a real server DB: the same
      automatic project resolved to `manual_learning` (prompt "Mode: manual_learning") before the fix and
      `automatic` after. Three existing tests had encoded the bug (default = automatic project, expected
      manual behaviour); they now use a `manual_project` fixture. `tests/test_project_mode.py` adds 12
      cases (automatic → auto-decide with no settings layer; manual_learning → waits + inbox; automatic +
      high risk still waits; task mode overriding both ways; plan prompt mode; learning trace; resolve()
      precedence incl. the project_id+task_id form Gateway.call uses); 7 of them fail with the fix
      reverted. `pytest -q` 195/195 green. `/code-review` (high): no bug in this diff; out-of-scope issues
      raised separately — manager decide/auto-decide paths ignore `project.paused` (pre-existing, more
      reachable now), settings-layer values are unvalidated (a typo'd `mode` overrides the project's).
      Real-model check with the owner's Claude account: BLOCKED — the local Claude Code CLI 2.1.185
      rejects the `claude_account` provider's `--permission-prompts none` ("unknown option"), so the plan
      call failed before reaching a model (ModelCall ok=false, 0 tokens, $0). Needs the owner's decision
      (CLI version vs. provider flag); not changed here.
- [x] 7. **Source quality, not just source presence.** The FACT guard (manager.py ~L68) only checks
      that a URL exists; a real run labeled a restaurant blog's survey as FACT. Add a deterministic
      source tier per URL (e.g. official/academic/stats bodies > app stores/company pages > blogs/
      unknown) and downgrade a FACT whose only sources are the lowest tier to INFERENCE with a
      visible note. Keep it deterministic (domain rules in a registry file), no extra model calls.
      Done 2026-09-28: `app/sources.py` gives each cited URL a tier from host-only rules in
      `registry/source_tiers.yaml` — 1 official/academic/statistics (restricted TLDs gov/edu/mil/int plus
      listed country suffixes and bodies), 2 app stores, review platforms, market-data firms, established
      publishers, 3 blog platforms, forums, social, Wikipedia and every unknown site. `_apply_evidence_guard`
      now also downgrades a FACT whose URLs are all tier 3 to INFERENCE with a note in its basis
      ("[downgraded from FACT: only low-tier sources (blog/forum/unknown): <hosts>]"), keeps the no-URL rule,
      and records `reason`/`hosts` per claim in the `evidence.downgraded` event; the rules file is validated
      at startup. Decisions: unknown = lowest (the owner's "blogs/unknown"); one better-than-lowest source
      keeps a FACT; a `blog.`/`blogs.` subdomain stays tier 3 even under a trusted domain; a company page
      counts as tier 2 only once its domain is listed. Trade-off: a competitor's own pricing page is
      downgraded until its domain is added; a model can still keep a FACT by citing an irrelevant trusted
      URL (relevance is not checked). `/code-review` (high) found and fixed 3 real issues before push: a
      label rule ("gov./ac./go. under any country code") trusted registrable names such as go.to (a redirect
      service) — replaced by an explicit suffix list; a bare single-label host (`https://gov/x`) got tier 1;
      internationalized domains could never match an entry (hosts are now normalized to punycode).
      Verified: `pytest -q` 243/243 green; tests/test_source_tiers.py covers 34 tier cases (incl. lookalike,
      userinfo, query-string and IP attacks), URL extraction from Persian prose and Markdown, registry
      validation, and the restaurant-blog case end to end through /run — which fails with the guard change
      reverted. No model calls.
- [ ] 8. **Inbox gap.** READY tasks with no checkpoint yet don't appear in the decision inbox, so
      the owner can't see what is waiting to start. Show them (kind "ready") with a one-tap
      "start" action in web/app.js; tests for the inbox API.
