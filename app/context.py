"""Context Builder (docs/ARCHITECTURE.md §25): give the model only what the current call needs.

Token rules:
- The system prompt is stable per (agent, skill set) so providers can cache it.
- Skills are loaded by name for the action at hand, never all at once.
- Dependency results go in as short summaries, never full histories.
- The volatile part is capped at `context.max_chars` from settings.
"""

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import settings_layers
from app.models import Agent, MemoryItem, RunEvent, Skill, Task


class ContextError(ValueError):
    pass


def system_prompt(session: Session, agent: Agent, skills: list[str]) -> str:
    if not_granted := set(skills) - set(agent.skills):
        raise ContextError(f"agent '{agent.name}' does not have skills {sorted(not_granted)}")
    bodies = {s.name: s.body for s in session.scalars(select(Skill).where(Skill.name.in_(skills)))}
    parts = [agent.instructions.strip()]
    for name in skills:  # caller's order, so the same action always yields the same prefix
        parts.append(f"## Skill: {name}\n{bodies[name].strip()}")
    return "\n\n".join(parts)


def agent_directory(session: Session) -> str:
    """One compact line per active agent — what the manager needs to delegate."""
    agents = session.scalars(select(Agent).where(Agent.active).order_by(Agent.name))
    return "\n".join(f"- {a.name}: {', '.join(a.capabilities)}" for a in agents)


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _summary(output: dict[str, Any] | None, limit: int) -> str:
    if not output:
        return "(no output)"
    text = output.get("summary") if isinstance(output.get("summary"), str) else json.dumps(
        output, ensure_ascii=False, sort_keys=True
    )
    return _truncate(text, limit)


def task_context(
    session: Session, task: Task, *, extra: str = "", settings: dict[str, Any] | None = None
) -> str:
    """`settings` lets a caller that already resolved layered settings for this task (e.g.
    app/manager.py::run_task, which also needs "mode") reuse it instead of resolving twice."""
    settings = settings if settings is not None else settings_layers.resolve(session, task_id=task.id)
    max_chars = int(settings["context"]["max_chars"])

    # Highest priority first; later sections are dropped when the budget runs out.
    sections = [
        f"Project goal: {task.project.goal}",
        f"Task: {task.title}\nInput: {json.dumps(task.input, ensure_ascii=False, sort_keys=True)}",
    ]
    if extra:
        sections.append(extra)
    # §24: retrieve only what's relevant, never the whole project memory — here, recency (§53: no
    # vector/semantic search yet). Not excluding this task's own prior memory: it can only exist
    # from an earlier run (memory is captured only after a run completes, always after this call
    # runs for that same execution), so on a redo (the reviewer's COMPLETED -> READY loop) the agent
    # should see what it found last time, not lose it.
    memories = session.scalars(
        select(MemoryItem)
        .where(MemoryItem.project_id == task.project_id)
        .order_by(MemoryItem.id.desc())
        .limit(5)
    ).all()
    if memories:
        # Capped the same way dependency results are below: a fixed 300 chars/item would let this
        # section alone exhaust a tight max_chars budget and starve the (higher-value) dependency
        # results appended after it, since the truncate-and-break loop drops everything past the
        # first section it has to cut.
        per_memory = max(100, (max_chars // 4) // len(memories))
        sections.append("Relevant project memory:\n" + "\n".join(
            f"- [{m.category.value}] {m.title}: {_truncate(m.content, per_memory)}" for m in reversed(memories)
        ))
    deps = session.scalars(select(Task).where(Task.id.in_(task.depends_on)).order_by(Task.id)).all() if task.depends_on else []
    if deps:
        per_dep = max(200, (max_chars // 2) // len(deps))
        sections.append("Results from earlier tasks:\n" + "\n".join(
            f"- {d.title}: {_summary(d.output, per_dep)}" for d in deps
        ))
    decisions = session.scalars(
        select(RunEvent)
        .where(RunEvent.project_id == task.project_id, RunEvent.type.like("decision.%"))
        .order_by(RunEvent.id.desc())
        .limit(5)
    ).all()
    if decisions:
        sections.append("User decisions:\n" + "\n".join(
            f"- {json.dumps(e.payload, ensure_ascii=False, sort_keys=True)}" for e in reversed(decisions)
        ))

    out, used = [], 0
    for section in sections:
        if used + len(section) > max_chars:
            remaining = max_chars - used
            if remaining > 100:
                out.append(section[: remaining - 1] + "…")
            break
        out.append(section)
        used += len(section) + 2
    return "\n\n".join(out)
