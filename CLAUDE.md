# Agent Factory — notes for Claude

- The source of truth for design is `docs/ARCHITECTURE.md` (Persian). Build in the order of its §52 phases;
  the README status table tracks progress — update it when a phase lands.
- `docs/AGENT_PRINCIPLES.md` defines the six engines (Persona, Knowledge Boundary, Action, Observation, Memory,
  Synthesis). They apply in full only to the dedicated Persona Agent (simulated user). Specialist agents take
  only Synthesis (suspend conclusions until evidence supports them) and Memory.
- Model gateway must support multiple providers from the start (owner's decision); no agent names a model ID.
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
