# Agent Factory

An **AI Venture Operating System**: a team of AI agents that understands a goal, plans it as a task graph,
runs specialists in parallel, reviews the result, and — in **Manual / Learning Mode** — teaches the user
how the problem was solved.

Full design: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

```text
USER → MODE → ORCHESTRATOR → TASK GRAPH → AGENT → MODEL → TOOLS → RESULT → REVIEW → MEMORY → USER
                                  ↑                         │
                                  └──── AGENT FACTORY ──────┘
                         MANUAL MODE → LEARNING TRACE
```

## Status

| Phase | Scope | Status |
|---|---|---|
| 1 — Foundation | API, DB, User / Project, Task graph, task state machine, event log | ✅ |
| Infra | Layered settings, agent & skill registry, model gateway with token/cost tracking and budgets, context builder | ✅ |
| 2 — Manager | Planner, approval gate, mode engine | ✅ (learning trace UX pending) |
| 3 — Agent Registry | Agent schema, capabilities, tools, permissions, versions | ✅ (base) |
| Agent Blueprint | Mandatory 14-section standard (`docs/AGENT_BLUEPRINT.md`), schema validator, guided interview | ✅ |
| 4 — First Agents | Research, Customer, Strategy, Product; task execution (`/tasks/{id}/run`) | ✅ (base — no tools yet) |
| 5 — Model Gateway | Provider abstraction, routing, cost tracking | ✅ (Anthropic, claude_account, Ollama, OpenAI) |
| 6 — Builder + Reviewer | GitHub, coding worker, PR, review loop | |
| 7 — Memory | Project state, decisions, retrieval, learning memory | ✅ (base — recency retrieval, no embeddings/pgvector yet) |
| 8 — Learning UX | Learning Trace, Ask Why, Try It Myself | |
| 9 — Agent Factory | Specialist spec → sandbox → evaluation → registry | |
| 10 — Idea Hunter | Scheduled opportunity discovery | |
| 11 — Scale | Queue workers, caching, observability, rate limits | |
| Human-guided factory (Issue #2) | PR7: external API — scoped keys that run one activated agent version of yours inside limits you set. PR6b: your own provider keys, kept encrypted, shown only masked, referenced as `cred:<id>`. PR6: workflows — agents connected by typed, validated edges, versioned, approved as one version, run as the project's tasks. PR5: deterministic build into an immutable agent version, evaluation of every test case (code check + judge model, within the build's budget, at most 3 rounds), activation only after PASS and the owner's approval, rollback. PR4: a missing specialist's step stays BLOCKED in the DAG; bounded step-by-step runs (limits, tool gateway, owner approvals, leases, crash reconciliation). PR3: design canvas, 14-section blueprint from the brief, per-build budget, light theme. PR2: adaptive intake, versioned brief, approvals. PR2a: sign-in, per-account access, Alembic migrations. PR1: four independent settings axes (UI language fa/en/tr, explanation level, code visibility; interaction policy with ceilings, quality target, reply language), coded API errors, three-language UI and interview; design docs and gap table (`docs/HUMAN_GUIDED_FACTORY.md`, `docs/FACTORY_UI_SPEC.md`, `docs/FACTORY_IMPLEMENTATION_PLAN.md`). all 7 PRs done | ✅ PR7 |
| UI shell | Admin panel wired to the real API — dashboard, decision inbox, projects (create/plan/checkpoints/chat/events/usage), agents (incl. training feedback), layered settings, benchmark comparison, observability, installable PWA | ✅ (`web/`, served at `/app`) |

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload     # http://localhost:8000/docs, http://localhost:8000/app for the UI
pytest
```

The UI (`/app`) creates the server's first account (its admin) automatically on first open (no setup step); see
"Sign-in" below for every later device and account. It is an installable
PWA (`web/manifest.json`, `web/icon.svg`) — "Add to Home Screen" on a phone gives it its own icon.

Uses SQLite by default. For PostgreSQL: `pip install -e ".[postgres]"` and set `DATABASE_URL`
(see `.env.example`).

### SQLite vs PostgreSQL: what a column refuses

SQLite stores any string in a `VARCHAR(n)` and any integer in an `INTEGER`; PostgreSQL (production) refuses what
doesn't fit: a 500 at the store, after the paid model call when the text came from a model, and again (paid again) on
every retry. So every value that reaches a column is checked against that column's own limit first
(`app/storable.py`): a request body is a `StorableIn` and a bad field is a 422 naming it (longer than its column,
a NUL or lone surrogate, NaN/Infinity inside a JSON object, an integer beyond 32 bits); a model's reply is a
`StorableOut` plus length validators on the classes whose text lands in a column; `limit(Model, "column")` is the one
place a limit is read from. `tests/postgres_limits.py` makes the suite's SQLite refuse what PostgreSQL refuses (every
test, every engine), and `tests/test_column_limits.py` audits every `String(n)` column — a new one fails the suite
until its writers are classified. `AGENT_FACTORY_TEST_PG_URL=postgresql+psycopg://… pytest tests/test_postgres_parity.py`
re-checks all of it against a real server (each test in a schema of its own, dropped afterwards).

There are no migrations yet (`create_all` only creates missing tables), so a column widened later is widened in an
existing PostgreSQL database at startup by `app/db.py::ensure_column_lengths` — like `ensure_indexes`: idempotent,
widen-only, never shrinking.

## Run with Docker

```bash
cp .env.example .env   # add ANTHROPIC_API_KEY once you have one
docker compose up --build
# http://localhost:8000/docs
```

Starts the API and a `postgres:16` container together (with a persisted volume and healthcheck).
The `api` service reads `DATABASE_URL` and `ANTHROPIC_API_KEY` from `.env`.

## Phase 1 API

| Method | Path | Purpose |
|---|---|---|
| POST | `/users` | Create user |
| POST | `/projects` | Create project (`mode`: `automatic` / `manual_learning`) |
| POST | `/projects/{id}/stage` | Advance project stage (IDEA → DISCOVERY → … → ITERATION) |
| POST | `/projects/{id}/pause` | Pause / resume. While paused nothing spends a model call or moves a task to `READY`/`RUNNING`: plan, approve, reject, checkpoint, decide, run, chat, a transition to `READY`/`RUNNING` and a stage advance answer 409 before doing anything, each logged as `pause.blocked`. The check (`app/pause.py`, reading `paused` fresh) runs at every entry point (`app/manager.py::ensure_active`; for a transition, its move check) and again as a backstop for a path that skips it: before any model call (`Gateway.call`, action `model_call`) and in every task move (`check_task_move`, action `transition`); approve, decide and a transition re-check under a lock as they commit, so a pause that lands mid-request never lets a move in after it. Every refusal rolls back the request's uncommitted work and answers 409 on any route. A call already in flight when the pause lands keeps its paid-for result, but its `READY`/`RUNNING` move is withheld (`pause.withheld`): a checkpoint is not auto-decided and waits for the owner; a finished run's dependents are promoted on resume |
| GET  | `/projects/{id}/events` | Audit log of every transition |
| POST | `/projects/{id}/tasks` | Create task with `depends_on` (DAG) |
| GET  | `/projects/{id}/tasks/runnable` | Tasks whose dependencies are done — safe to run in parallel |
| POST | `/projects/{id}/tasks/{tid}/transition` | Move task through the state machine |

## Phase 2 API — Manager

| Method | Path | Purpose |
|---|---|---|
| POST | `/projects/{id}/plan` | One model call → task graph; unknown agents become `specialist.requested`; waits for approval |
| POST | `/projects/{id}/plan/approve` | Move dependency-free `CREATED` tasks to `READY` |
| POST | `/projects/{id}/plan/reject` | Re-plan with `{feedback}` and, with the new plan, delete the proposed tasks that were still `CREATED` — one unit: nothing is rejected until the re-plan's call succeeded and passed every check (a 402/502/422/409 leaves the old plan as it was), and only if that plan was not decided meanwhile (approved, planned again or rejected again from another tab: 409) |
| POST | `/projects/{id}/tasks/{tid}/checkpoint` | Model proposes options + a recommendation; auto-decided unless mode/risk requires the user. The call takes seconds, so the task is read again under the project lock once it returns (`app/pause.py::lock_task`): if it moved on meanwhile (cancelled, decided from another tab or device, put on hold) the auto-decision is withheld — the paid-for checkpoint is kept, `task.changed_meanwhile` is recorded and the owner decides |
| POST | `/projects/{id}/tasks/{tid}/decide` | `{option, note?}` → records the decision, moves the task `READY → RUNNING` |
| POST | `/projects/{id}/tasks/{tid}/run` | Executes a `RUNNING` task with its owner agent (Phase 4): one `Gateway.call` (agent's role + skills + `context.task_context`, which includes the chosen option and dependency summaries) → `{summary, findings[{claim,type,basis}], lesson, next}`; stores the output, moves `RUNNING → COMPLETED`, and any dependent task whose dependencies are now all done becomes `READY`. The task is read again under the project lock once the call returns: a task that is no longer the one the call began with (failed, held or completed by the owner meanwhile, retried, or the same task run from another device and completed first) takes none of the result — 409, `task.changed_meanwhile` is recorded, and only the paid call stays logged (its `ModelCall`) |

Every step above is a single, budget-checked `Gateway.call` (`BudgetExceeded` → 402, `ProviderError` → 502;
a paused project → 409 before any call). In automatic mode a checkpoint is auto-decided only when the risk's
approval rule is `auto` or `manager`; any other value waits for the owner.

| Method | Path | Purpose |
|---|---|---|
| POST | `/projects/{id}/chat` | Owner ↔ manager chat: one `Gateway.call` (goal + compact task list + last 8 messages) → `{reply, suggested_action}`; the UI only ever shows a button for the suggested action, never auto-executes it. The owner's message is stored only together with the reply, after the call (owner decision d19): a refused or failed turn (409/402/502/422) adds nothing to the chat, and nothing is written while the model runs (on SQLite that would hold the database-wide write lock and fail the owner's pause) |
| GET  | `/projects/{id}/chat` | Chat history for the project |
| GET  | `/inbox` | Everything waiting on the owner across every project — plans awaiting approval, checkpoints awaiting a decision, and `READY` tasks with no checkpoint yet (kind `ready`; the panel's one-tap «شروع» requests the task's checkpoint). A paused project lists nothing until it is resumed (owner decision d17); its own page still shows its plan and tasks — derived from existing events, nothing new stored |
| POST | `/feedback` | Owner feedback (👍/👎 + optional note) on an agent's output — plan, checkpoint or chat reply |
| GET  | `/agents/{name}/feedback` | Feedback recorded for one agent; turning it into skill updates is a later phase |

## Phase 4 — First Specialists

Four specialists ship in `registry/agents/` (`researcher`, `customer`, `strategy`, `product`), each a valid
`docs/AGENT_BLUEPRINT.md` specialist (Synthesis + Memory/Log only — no Persona engine, `observation.record`
fixed to `["runevent"]`), reasoning from `context.task_context` only — no tools yet, no browsing. Every
specialist shares the `task-execution` skill (the `/run` output shape) and `evidence` (FACT/INFERENCE/
HYPOTHESIS, never invented sources), plus one domain skill (`research-method`, `customer-voice`,
`strategy-framing`, `product-definition`). The web panel shows an «اجرا» button on `RUNNING` tasks and
renders the result with evidence chips per finding. A deterministic guard downgrades a FACT to INFERENCE,
with a note in its basis, when it cites no URL or only lowest-tier sources (blogs, forums, social, unknown
sites); tiers come from domain rules in `registry/source_tiers.yaml` (`app/sources.py`), no model calls.

## Phase 7 API — Memory

Every completed task (`/tasks/{id}/run`) writes a `MemoryItem` (Project Memory, §24 — one of `brief|
decisions|research|customer|strategy|product|technical|experiments|learning`, category taken from the
owner agent when one maps to it, `technical` otherwise) and, only in `manual_learning` mode, a
`LearningTrace` (§26 — the task's `lesson`, kept separate from its output). `app/context.py::task_context`
retrieves the project's 5 most recent memory items into every task's context — recency, not semantic
search (§53: no vector DB yet).

| Method | Path | Purpose |
|---|---|---|
| GET | `/projects/{id}/memory?category=` | List Project Memory items, newest first |
| POST | `/projects/{id}/memory` | Add one manually (e.g. an initial brief) — `{category, title, content, task_id?}` |
| GET | `/projects/{id}/learning` | List Learning Traces (read-only — captured automatically, not authored) |

The web panel's project detail page has a «حافظه» tab: add a memory note, and see memory + learning
entries merged into one chronological feed.

## Agent Blueprint API

Every agent built in the factory must satisfy `docs/AGENT_BLUEPRINT.md` — the mandatory 14-section
standard derived from the owner's architecture image, mapped onto what already exists (gateway,
context builder, `RunEvent`/`ModelCall`, registry). Validation is deterministic, no model calls
(`app/blueprint.py`, schema in `registry/blueprint.schema.json`).

| Method | Path | Purpose |
|---|---|---|
| POST | `/blueprints/validate` | Deterministic check of a blueprint against the 14 sections; returns `{valid, issues}` |
| GET  | `/interview` | Next guided-interview question given `answers` collected so far (or the completed blueprint) |
| POST | `/interview/answer` | Record one answer, return the next question or the completed blueprint |

Interview questions live as one YAML file per section in `registry/interview/` (loader: `app/interview.py`).
A model is used only to turn a free-text answer into its field (`registry/skills/agent-intake.md`) —
which question comes next and how answers become a blueprint are pure code. The web UI's
"+ ساخت Agent جدید" button (`/app#agents/new`) walks the same interview one question per screen.

## Infrastructure API

| Method | Path | Purpose |
|---|---|---|
| POST | `/workspaces` | Create workspace |
| GET | `/workspaces` | List workspaces |
| GET/PUT | `/settings/{global\|workspace\|project\|task}/{id}` | Read / replace one settings layer (global id = 0) |
| GET | `/settings/resolved?task_id=` | Effective settings: defaults ← global ← workspace ← project's own `mode`/`budget` ← project layer ← task's own `mode` (if set) ← task layer |
| GET/PATCH | `/users/{id}/preferences` | The owner's display preferences: `ui_locale` (fa/en/tr), `audience_level` (ordinary/professional/developer), `code_visibility`; `null` = not chosen yet, `effective` = what the UI uses. Display only — grants nothing |
| GET | `/agents`, `/agents/{name}`, `?capability=` | Agent registry; POST `/agents` registers after validation |
| GET | `/skills`, `/skills/{name}` | Skills (summary / full body) |
| GET | `/projects/{id}/usage` | Tokens, cost and budget for a project |
| GET | `/projects/{id}/model_calls` | Every model call for a project (role, provider, model, tokens, cost) |

Agents and skills are defined as files in `registry/` and synced at startup.
Model calls go through `app/gateway` (role → provider/model from settings, every call logged with tokens and
cost). Before a paid call runs, its estimated worst case (its prompt as input plus every output token; web-search
fees and the tokens of search results are not priced in) is checked against the task's and the project's budgets
together with the paid calls already in flight, and held while it runs: calls started at the same time are
checked against one another, a refusal is `budget.exceeded` (402), and a hold left by a process that stopped
mid-call stops counting after `HOLD_TTL`. Nothing (no transaction, no SQLite write lock) stays open while a model
runs. `app/context.py` builds the smallest prompt a call needs.

### Interaction policy, quality target, reply language (settings layers)

`interaction_policy` is how often the system stops for the owner — every mode is interactive (`docs/HUMAN_GUIDED_FACTORY.md`):
`step_by_step` (every step; the default for building an agent), `milestone` (the main points), `delegated_bounded` (only
inside an approved scope, stopping on ambiguity). Unless a layer sets it, it follows the project's `mode`
(`manual_learning` → `step_by_step`, which no layer can loosen; `automatic` → `milestone`). `max_interaction_policy` is a
ceiling: the strictest one any layer sets wins, so a project or task can't loosen an owner-wide limit; anything unknown
counts as `step_by_step`. A checkpoint is auto-decided only when mode is `automatic`, the policy isn't `step_by_step` and
the risk's approval rule allows it. `quality_target` (`prototype` / `production_candidate`) sets evaluation depth, never
authority. `output_locale` (`auto` = the owner's UI language, or `fa`/`en`/`tr`) adds a `Reply language: …` line to the
plan, chat and task prompts (user content only, so system prompts stay cacheable); none when the owner never chose a
language.

### Errors

Every error body is `{"detail": …, "code": …, "params": {…}}` (`app/errors.py`): `detail` stays English (logs, existing
clients); the UI shows `code` in the owner's language (`web/i18n.js`, `err.<code>`), plus the detail when it carries the
reason. Codes: `not_found` (`params.resource`), `invalid_request`, `settings_invalid`, `unknown_dependencies`,
`agent_invalid`, `request_refused`, `email_taken`, `transition_refused`, `project_paused`, `budget_exceeded`,
`provider_unavailable`; a plain HTTP error gets one by status.

### Building an agent: the intake and the brief (issue #2 PR2, owner decision d28)

"+ New agent" opens a build (`/factory/sessions`, app/factory.py, questions in `registry/factory/brief.yaml`, fa/en/tr):
one sentence ("what, for whom, what should it do?"), then five short questions (who, the problem, a real input, the
expected output, one measurable criterion), one per turn; limits, sources, permissions and budget are proposed. Each
question offers "I don't know — suggest"; a vague answer is asked again; a contradiction (the web mentioned, internet
forbidden) becomes the next question. Every change is a new immutable brief version with each field's status
(draft / proposed / confirmed) and source (user / default / suggestion / unknown). Approving binds to one exact version
(`brief_version`) and confirms it; changing anything afterwards invalidates that approval. Every change names the
build's `expected_revision` (another tab's change in between is a 409 `stale_revision`) and a `request_id` (a double
click applies once). Pause / resume / cancel; every change is a RunEvent with its actor. No model call: deterministic.

| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/factory/sessions` | Your builds / start one (`request_id`) |
| GET | `/factory/sessions/{id}?locale=` | Stage, brief, next question, "our understanding" summary, approval |
| POST | `/factory/sessions/{id}/answer` | `{field, value | dont_know, expected_revision, request_id}` |
| POST | `/factory/sessions/{id}/approve` | `{brief_version, expected_revision, request_id}` |
| POST | `/factory/sessions/{id}/request-changes`, `/pause`, `/cancel` | |

### Designing the agent (issue #2 PR3)

Approving the brief draws the agent's 14-section Blueprint from it, deterministically and free (`app/design.py`): what an
ordinary owner isn't asked (model role, storage, deployment, what is recorded) is filled by the system per the standard.
The build page shows it as a canvas — the goal in the centre, eight branches (identity, I/O & actions, knowledge,
tools, memory, model, interaction & permissions, evaluation) covering all 14 sections — each proposed, confirmed or
incomplete (icon + text, not colour alone); on a phone, a step list. Each branch can be edited (a new immutable
`BlueprintRevision`, the branch back to proposed), confirmed (only without issues), or undone to an earlier version.
Every permission starts off; a tool needs its permission; the approval gate can't be looser than the build's
interaction policy. "Improve with AI" is the only model call of a build, made only on request (role `cheap`, schema-
validated) and held against the build's own budget (`FactorySession.budget_usd`, from `budget.task_usd`; 402 when over;
`ModelCall.factory_session_id`); its values come back as proposals. Approving the design binds to one exact version made
from the currently approved brief and moves the build to BUILDING (PR5). The UI's default look is theme A (light,
`#F8FAFC` / `#172033` / `#14B8A6`); the earlier dark theme is a choice in Settings.

| Method | Path | Purpose |
|---|---|---|
| POST | `/factory/sessions/{id}/design/edit` | `{branch, changes: {path: value}, expected_revision, request_id}` |
| POST | `/factory/sessions/{id}/design/confirm` | `{branch, blueprint_version, …}` |
| POST | `/factory/sessions/{id}/design/revert` | `{to_version, …}` (undo) |
| POST | `/factory/sessions/{id}/design/assist` | one paid suggestion within the build's budget |
| POST | `/factory/sessions/{id}/design/approve` | `{blueprint_version, …}` |

### External API (issue #2 PR7)

Settings → API keys makes a key for an outside program. Making it is **your standing approval** of exactly: one agent version you
built, evaluated and activated here, one of your projects, its scopes (`run`, `read`, `answer`) and its limits (runs per 24 hours,
default 20; dollars per run, default 0.50). The secret (`afk_…`) is shown once; only its hash is kept. Calls run in the bounded
runtime: a read-only tool may run, but **any tool action or unclear outcome waits for you in the inbox**, and the program can only
answer the agent's questions. There is no external route that approves a plan, checkpoint, design, activation or tool. Every call
re-checks the key, that the agent is still that active version, that the project isn't paused, and the quota — before any spend.

```bash
curl -X POST http://localhost:8000/api/v1/runs -H "Authorization: Bearer $KEY" \
     -H "Content-Type: application/json" -d '{"input": "…", "request_id": "unique-id-1"}'
curl http://localhost:8000/api/v1/runs/42 -H "Authorization: Bearer $KEY"      # state, waiting_for, question, result
curl -X POST http://localhost:8000/api/v1/runs/42/answer -H "Authorization: Bearer $KEY" \
     -H "Content-Type: application/json" -d '{"text": "March"}'
```

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/me` | what the key may do; runs used in the last 24 hours |
| POST | `/api/v1/runs` | `{input, request_id}` — start a run (same `request_id` → same run) |
| GET | `/api/v1/runs/{id}` | `state` (queued/running/waiting/completed/stopped), `waiting_for` (`answer` / `owner`), `question`, `result` |
| POST | `/api/v1/runs/{id}/answer` | `{text}` — only for a question the agent asked |
| POST | `/api/v1/runs/{id}/cancel` | stop the run |
| GET/POST/DELETE | `/api-keys` | the owner's side (session): list, make (secret once), revoke |

Put a rate limit in front of the server (proxy/WAF) for failed key attempts; the secret has 256 bits, but the API does not throttle.

### Connections: your own API keys (issue #2 PR6b)

Settings → Connections keeps a provider key (Anthropic, OpenAI) **encrypted** on the server: add it once, and afterwards only
`••••` plus its last four characters, its name, provider and the result of its last test are ever shown. Elsewhere it is a
reference, `cred:<id>`, chosen per model role in the model settings; the key is used only for its owner's own calls and
its own provider, and never appears in a log, event, error, settings layer, blueprint or workflow. *Test* makes one free
request to the provider's own list-models endpoint, only when pressed, and shows what came back. Revoking wipes the key.
**Setup:** set `AGENT_FACTORY_SECRET_KEY` (at least 32 characters, see `.env.example`) and `pip install -e .` (the new
`cryptography` dependency); without them keys can't be added and the page says why. Back the secret up: without it stored
keys can't be read.

| Method | Path | Purpose |
|---|---|---|
| GET | `/credentials` | your connections, masked, and whether keys can be kept here |
| POST | `/credentials` | `{name, provider, key, request_id}` — the only time a key is sent |
| POST | `/credentials/{id}/test` | one free request to the provider; records what it answered |
| DELETE | `/credentials/{id}` | revoke (wipes the key) |

### Workflows (issue #2 PR6)

Agents are connected on a canvas (`#workflows`): `data` edges carry one agent's result field into the next one's input
through an explicit mapping (a list becomes text only with an explicit `join`), `model` edges give an agent its model
role, `tool` edges only a tool the agent has, has the permission for and the Tool Gateway implements, `knowledge` edges
only a source the agent may use. The validator refuses cycles, wrong ports, type mismatches and missing required inputs
before an approval. Every semantic change is a new immutable version; moving a node changes nothing but its place;
**saving never runs anything**. Nodes pin an exact agent version (a built agent's version that passed, or an active
registry agent). *Run* turns the owner-approved version into the project's tasks through the manager's plan (dependencies
from the data edges, a missing agent is a BLOCKED task), which still waits for the owner's plan approval.

| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/workflows` | list mine / create |
| GET | `/workflows/{id}` | the latest version, issues, layout, catalog of what can be pinned |
| PUT | `/workflows/{id}/draft` | `{nodes, edges, expected_revision, request_id}` — save a version (nothing runs) |
| PUT | `/workflows/{id}/layout` | node positions (display only) |
| POST | `/workflows/{id}/approve` | `{version, …}` — the owner approves exactly that version |
| POST | `/workflows/{id}/run` | `{version, project_id, request_id}` — create the project's tasks |

### Building, evaluating, activating (issue #2 PR5)

After the design is approved, **Build** turns it into an immutable `AgentVersion` (free, deterministic): the
registry spec plus instructions written from the design. Nothing is active. **Run the evaluation** (paid, only on the
owner's click, held against the build's budget) sends every test case of the design to the candidate; a reply that
breaks the agent's JSON contract fails by code, the rest are judged PASS/FAIL with a reason by a judge model (role
`cheap`, skill `evaluate-agent`). All PASS → the owner activates exactly that version; otherwise the build goes back
to the design with the failed cases (at most 3 evaluation rounds per build). Activation registers the agent; a newer
version retires the older one, and `POST /factory/agents/{name}/rollback` brings back one that was active before.
An admin can still register an agent directly (`POST /agents`); an agent the factory builds is active only after a
PASS and the owner's approval.

| Method | Path | Purpose |
|---|---|---|
| POST | `/factory/sessions/{id}/build` | approved design → candidate version (free) |
| POST | `/factory/sessions/{id}/evaluate` | paid evaluation of every test case |
| POST | `/factory/sessions/{id}/activate` | `{agent_version, …}` — the owner's final approval |
| GET | `/factory/agents/{name}/versions` | the agent's versions and verdicts |
| POST | `/factory/agents/{name}/rollback` | `{to_version, request_id}` |

### Bounded runs (issue #2 PR4)

A plan step whose agent doesn't exist is kept as a `BLOCKED` task (`reason: missing_specialist`) with all its
dependency edges, so nothing that depends on it starts; once the agent is registered, `POST …/tasks/{id}/unblock`.
A RUNNING task can be run step by step (`app/runtime.py`): a run snapshots its limits (steps, tool calls, time,
failures), interaction policy, the agent's permissions and tools; a worker (`app/worker.py`) claims it with a lease and
asks the agent's model for ONE action per step — finish, use a tool, or ask the owner — and code decides. A tool runs
only if the tool gateway implements it (today `memory_search`, read-only), the agent has it, its permission is granted
and its input is valid; under `step_by_step` the owner approves each tool action first. Tool output and the owner's
answers reach the model as data, never as instructions. Waiting on the owner holds no worker and no transaction; the
answer continues the same run. Every stop has a reason (`step_limit`, `time_limit`, `tool_limit`, `budget_exhausted`,
`model_failed`, `schema_mismatch`, `run_cancelled`, `task_moved`). A worker that dies mid-tool leaves it `started`; the
next one never re-runs it — it becomes `unknown` and the owner says whether it happened. The app runs
`AGENT_FACTORY_WORKERS` worker threads (default 1; set 0 and run `python -m app.worker` separately to scale out).

| Method | Path | Purpose |
|---|---|---|
| POST | `/projects/{id}/tasks/{tid}/unblock` | BLOCKED → CREATED (READY when allowed) once the agent exists |
| POST | `/projects/{id}/tasks/{tid}/runs` | queue a bounded run of a RUNNING task |
| GET | `/projects/{id}/tasks/{tid}/runs`, `…/runs/{rid}` | the run with its steps and tool executions |
| POST | `/projects/{id}/tasks/{tid}/runs/{rid}/respond` | `{text}` / `{approve}` / `{happened}` — what the run waits on |
| POST | `/projects/{id}/tasks/{tid}/runs/{rid}/cancel` | stop it (not an undo) |

### Sign-in and access (owner decision d33)

No passwords. The first visitor of a server gets its first account, which is the **admin**. A signed-in browser holds
an HttpOnly, SameSite=Strict session cookie (`/auth/me`, `/auth/logout`); the database keeps only its SHA-256.
Another device signs in with a **one-time code** (Settings → "Sign in on another device": `POST /auth/login-codes`,
then `POST /auth/login`; 10 minutes, single use). Only the admin creates accounts (`POST /users` while signed in) and
hands each over with a code for it. An account from before sign-in existed is taken over once (`POST /auth/claim`):
automatically from the server's own machine, elsewhere with `AGENT_FACTORY_OWNER_TOKEN`.

Every account sees only its own projects, tasks, workspaces, settings layers, memory, chat, feedback and preferences;
another account's id answers the same 404 as a missing one. Only the admin changes what every account shares: the
global settings layer, the agent registry, benchmarks. `tests/test_auth.py` fails if any route is reachable without a
session (except the sign-in routes and `/health`).

### Database migrations (owner decision d34)

Alembic (`migrations/`). The app migrates at startup (`app/db.py::migrate`): an empty database, one made by the old
`create_all` startup (no history: the baseline creates only what is missing) and an up-to-date one all end at the
models' schema, rows kept — checked on SQLite in `tests/test_migrations.py` and on PostgreSQL 16. New change:
`DATABASE_URL=… alembic revision --autogenerate -m "…"`, then review the generated file.

### Languages

The UI speaks Persian, English and Turkish (`web/i18n.js`, key-based, English fallback; `app.js` holds no text). The
language is chosen on first entry and changed from the top bar or Settings; it is stored on the account (this browser
only if the server can't be reached) and changing it resets no answer, approval or stage. Interview questions carry
`{fa, en, tr}` text (`GET /interview?locale=`); answers are keyed by target, so changing language mid-interview keeps them.

### Model access

Each role's provider is either set explicitly (`models.<role>.provider`) or follows the
top-level `model_access` switch (`GET/PUT /settings/global/0`), which applies to every role
that doesn't override it:

- **`api_key`** (provider `anthropic`) — set `ANTHROPIC_API_KEY` and `pip install -e ".[anthropic]"`.
  Needed to serve other people, since it doesn't depend on any one person being logged in.
- **`claude_account`** (provider `claude_account`, the default) — uses the owner's own Claude
  subscription, no API key: the gateway shells out to the Claude Code CLI in headless mode
  (`claude -p --output-format json`). For the owner's personal use only; install the CLI
  ([code.claude.com/docs/en/setup](https://code.claude.com/docs/en/setup)) and either run `claude`
  once locally to log in, or set `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`) in Docker
  or on a server with no interactive login — see `.env.example`.
- **`ollama`** (provider `ollama`) — a local Ollama server, free, no account: the gateway calls its
  HTTP API (`POST /api/chat`, `stream: false`; a JSON schema goes in `format` for structured output).
  Base URL from `OLLAMA_BASE_URL` (default `http://localhost:11434`). Run it yourself, or
  `docker compose --profile ollama up` (not started by default) and set `OLLAMA_BASE_URL=http://ollama:11434`;
  pull a model first with `docker compose exec ollama ollama pull <model>`.

A fourth provider, **`openai`** (owner decision d7 — multi-provider), is available per role — set an
explicit `models.<role>.provider: "openai"` (it isn't one of the `model_access` switch's values, since
that switch only covers whole-project convenience defaults). Set `OPENAI_API_KEY` and
`pip install -e ".[openai]"`; uses the official `openai` SDK's Chat Completions API
(`max_completion_tokens`, `response_format: {"type": "json_schema", ...}` for structured output).

Task states: `CREATED → READY → RUNNING → (WAITING) → COMPLETED → REVIEWED`, with
`FAILED → READY` (retry, bounded by `max_retries`) or `→ ESCALATED`, and `COMPLETED → READY`
for the reviewer's `CHANGES_REQUIRED` loop.
