# Agent Factory — notes for Claude

- The source of truth for design is `docs/ARCHITECTURE.md` (Persian). Build in the order of its §52 phases;
  the README status table tracks progress — update it when a phase lands.
- `docs/AGENT_PRINCIPLES.md` defines the six engines (Persona, Knowledge Boundary, Action, Observation, Memory,
  Synthesis). They apply in full only to the dedicated Persona Agent (simulated user). Specialist agents take
  only Synthesis (suspend conclusions until evidence supports them) and Memory.
- All model calls go through `app/gateway` (never a provider SDK directly). Agents name a role; settings map
  role → provider/model. Every call is budget-checked and logged in `ModelCall`.
- Token discipline (owner's hard requirement): deterministic logic stays in code; load only the skills an
  action needs (`context.system_prompt`); keep system prompts stable for caching; pass dependency summaries,
  not histories (`context.task_context`); cap output tokens via settings.
- Agents and skills live as files in `registry/`; keep skills short.
- Every product decision is logged in the owner's decision-log artifact; ask the owner before each step and
  state the chosen path ("این مسیر را می‌رویم").
- Respect §53 ("what not to build in V1"): no Kubernetes, no microservices, no free agent-to-agent calls,
  no vector DB yet. Agents never call each other; everything goes through the orchestrator (§5).
- Stack: Python 3.11, FastAPI, SQLAlchemy 2 (typed `Mapped`), Pydantic 2. SQLite for dev/tests, PostgreSQL in prod.
- Every state change must write a `RunEvent` (`app/events.py`). Transition rules live in `app/state_machine.py`
  as pure functions; routers only apply them.
- Agents must not depend on concrete model IDs — go through the model gateway (Phase 5).
- Run tests: `pytest` (in `.venv`: `python -m venv .venv && .venv/bin/pip install -e ".[dev]"`).
- The user works from a phone and prefers Persian replies.

## Skills playbook (invoke the matching skill at that moment — never load them all up front)

| When | Skill / plugin |
|---|---|
| Ideation, judging an idea, market or competitor critique | `anthropic-skills:product-business-review` (Persian report) |
| Research across many sources before a product decision | `anthropic-skills:deep-research` |
| Any code that calls Claude (gateway, prompts, caching, cost) | `claude-api` (and `/claude-api cost-optimize` when spend grows) |
| Building an eval / "is the manager's plan good?" / Benchmark tab | `/claude-api build-eval`, then `/claude-api hillclimb` |
| Before every push: correctness | `/code-review` on the branch diff |
| Before every push touching auth, keys, tools, permissions | `/security-review` |
| After a feature lands: tidy the code | `/simplify` |
| Seeing the app actually run | `run` |
| New cloud sessions should install deps and run tests on start | `session-start-hook` |
| New specialist agent or skill for the registry | `anthropic-skills:skill-creator` (keep skill files short) |
| Pages, dashboards, diagrams for the owner | `artifact-design`, `artifact-diagramming`, `dataviz`; `/design` for canvases |
| Architecture / system-design / testing strategy (if the owner enabled it) | `engineering` plugin |

Rules: one skill per need, only when that step happens (token discipline). Record product decisions in the
decision log; ask the owner once per phase with all questions batched.
