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
| UI shell | Admin panel wired to the real API — dashboard, decision inbox, projects (create/plan/checkpoints/chat/events/usage), agents (incl. training feedback), layered settings, benchmark comparison, observability, installable PWA | ✅ (`web/`, served at `/app`) |

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload     # http://localhost:8000/docs, http://localhost:8000/app for the UI
pytest
```

The UI (`/app`) creates a default owner automatically on first open (no setup step) and is an installable
PWA (`web/manifest.json`, `web/icon.svg`) — "Add to Home Screen" on a phone gives it its own icon.

Uses SQLite by default. For PostgreSQL: `pip install -e ".[postgres]"` and set `DATABASE_URL`
(see `.env.example`).

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
| POST | `/projects/{id}/pause` | Pause / resume. While paused nothing spends a model call or moves a task to `READY`/`RUNNING`: plan, approve, reject, checkpoint, decide, run, chat, a transition to `READY`/`RUNNING` and a stage advance answer 409 before doing anything, each logged as `pause.blocked` (one check, `app/pause.py::refuse_if_paused`, reading `paused` fresh: `app/manager.py::ensure_active` runs it at every entry point, and `Gateway.call` again before any model call as a backstop for a path that skips it — action `model_call`, 409 on any route). A call already in flight when the pause lands keeps its paid-for result, but its `READY`/`RUNNING` move is withheld (`pause.withheld`): a checkpoint is not auto-decided and waits for the owner; a finished run's dependents are promoted on resume |
| GET  | `/projects/{id}/events` | Audit log of every transition |
| POST | `/projects/{id}/tasks` | Create task with `depends_on` (DAG) |
| GET  | `/projects/{id}/tasks/runnable` | Tasks whose dependencies are done — safe to run in parallel |
| POST | `/projects/{id}/tasks/{tid}/transition` | Move task through the state machine |

## Phase 2 API — Manager

| Method | Path | Purpose |
|---|---|---|
| POST | `/projects/{id}/plan` | One model call → task graph; unknown agents become `specialist.requested`; waits for approval |
| POST | `/projects/{id}/plan/approve` | Move dependency-free `CREATED` tasks to `READY` |
| POST | `/projects/{id}/plan/reject` | Delete the proposed tasks and re-plan with `{feedback}` |
| POST | `/projects/{id}/tasks/{tid}/checkpoint` | Model proposes options + a recommendation; auto-decided unless mode/risk requires the user |
| POST | `/projects/{id}/tasks/{tid}/decide` | `{option, note?}` → records the decision, moves the task `READY → RUNNING` |
| POST | `/projects/{id}/tasks/{tid}/run` | Executes a `RUNNING` task with its owner agent (Phase 4): one `Gateway.call` (agent's role + skills + `context.task_context`, which includes the chosen option and dependency summaries) → `{summary, findings[{claim,type,basis}], lesson, next}`; stores the output, moves `RUNNING → COMPLETED`, and any dependent task whose dependencies are now all done becomes `READY` |

Every step above is a single, budget-checked `Gateway.call` (`BudgetExceeded` → 402, `ProviderError` → 502;
a paused project → 409 before any call). In automatic mode a checkpoint is auto-decided only when the risk's
approval rule is `auto` or `manager`; any other value waits for the owner.

| Method | Path | Purpose |
|---|---|---|
| POST | `/projects/{id}/chat` | Owner ↔ manager chat: one `Gateway.call` (goal + compact task list + last 8 messages) → `{reply, suggested_action}`; the UI only ever shows a button for the suggested action, never auto-executes it |
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
| GET | `/agents`, `/agents/{name}`, `?capability=` | Agent registry; POST `/agents` registers after validation |
| GET | `/skills`, `/skills/{name}` | Skills (summary / full body) |
| GET | `/projects/{id}/usage` | Tokens, cost and budget for a project |
| GET | `/projects/{id}/model_calls` | Every model call for a project (role, provider, model, tokens, cost) |

Agents and skills are defined as files in `registry/` and synced at startup.
Model calls go through `app/gateway` (role → provider/model from settings, budget check first,
every call logged with tokens and cost). `app/context.py` builds the smallest prompt a call needs.

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
