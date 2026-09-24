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
| 2 — Manager | Planner, approval gate, mode engine | ⏳ next |
| 3 — Agent Registry | Agent schema, capabilities, tools, permissions, versions | |
| 4 — First Agents | Research, Customer, Strategy, Product | |
| 5 — Model Gateway | Provider abstraction, routing, cost tracking | |
| 6 — Builder + Reviewer | GitHub, coding worker, PR, review loop | |
| 7 — Memory | Project state, decisions, retrieval, learning memory | |
| 8 — Learning UX | Learning Trace, Ask Why, Try It Myself | |
| 9 — Agent Factory | Specialist spec → sandbox → evaluation → registry | |
| 10 — Idea Hunter | Scheduled opportunity discovery | |
| 11 — Scale | Queue workers, caching, observability, rate limits | |

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload     # http://localhost:8000/docs
pytest
```

Uses SQLite by default. For PostgreSQL: `pip install -e ".[postgres]"` and set `DATABASE_URL`
(see `.env.example`).

## Phase 1 API

| Method | Path | Purpose |
|---|---|---|
| POST | `/users` | Create user |
| POST | `/projects` | Create project (`mode`: `automatic` / `manual_learning`) |
| POST | `/projects/{id}/stage` | Advance project stage (IDEA → DISCOVERY → … → ITERATION) |
| POST | `/projects/{id}/pause` | Pause / resume |
| GET  | `/projects/{id}/events` | Audit log of every transition |
| POST | `/projects/{id}/tasks` | Create task with `depends_on` (DAG) |
| GET  | `/projects/{id}/tasks/runnable` | Tasks whose dependencies are done — safe to run in parallel |
| POST | `/projects/{id}/tasks/{tid}/transition` | Move task through the state machine |

Task states: `CREATED → READY → RUNNING → (WAITING) → COMPLETED → REVIEWED`, with
`FAILED → READY` (retry, bounded by `max_retries`) or `→ ESCALATED`, and `COMPLETED → READY`
for the reviewer's `CHANGES_REQUIRED` loop.
