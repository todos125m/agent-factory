"""Phase 7 — Memory (docs/ARCHITECTURE.md §24-26): long-term Project Memory (selective, retrieved
by app/context.py, never handed over whole) and Learning Memory (a lesson captured in manual_learning
mode, kept separate from the task's own output).

Deliberately simple for V1 (§53 — no vector DB yet): retrieval is recency within a project, not
semantic search. A `MemoryItem.content`/embedding column can be added later without breaking this.
"""

from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.events import record_event
from app.models import LearningTrace, MemoryCategory, MemoryItem, Project, Task

if TYPE_CHECKING:
    from app.manager import TaskRunOut

# Which Project Memory category a specialist's own findings belong under (§24's category list).
# An agent with no entry here (e.g. a future coding/builder specialist) falls back to "technical".
AGENT_MEMORY_CATEGORY: dict[str, MemoryCategory] = {
    "researcher": MemoryCategory.RESEARCH,
    "customer": MemoryCategory.CUSTOMER,
    "strategy": MemoryCategory.STRATEGY,
    "product": MemoryCategory.PRODUCT,
}


def capture_from_task_output(
    session: Session, project: Project, task: Task, agent_name: str, result: "TaskRunOut", *, mode: str
) -> None:
    """Called once a task's result is validated (app/manager.py::run_task). Always records the
    finding as Project Memory; only records a Learning Trace in manual_learning mode (§26).

    `mode` is the caller's already-resolved settings "mode" (the same one checkpoints gate on — see
    app/manager.py::create_checkpoint), which folds in `Project.mode`, `Task.mode` and any settings-layer
    override (precedence in app/settings_layers.py::resolve). Taking it as a parameter avoids a second
    settings_layers.resolve() for the same task within the same run_task call.
    """
    category = AGENT_MEMORY_CATEGORY.get(agent_name, MemoryCategory.TECHNICAL)
    item = MemoryItem(
        project_id=project.id, task_id=task.id, category=category, title=task.title, content=result.summary,
    )
    session.add(item)
    record_event(session, project.id, "memory.captured", task.id, category=category.value, title=task.title)

    if mode == "manual_learning" and result.lesson:
        trace = LearningTrace(project_id=project.id, task_id=task.id, concept=task.title, explanation=result.lesson)
        session.add(trace)
        record_event(session, project.id, "learning.captured", task.id, concept=task.title)


def add_memory_item(session: Session, project: Project, category: MemoryCategory, title: str, content: str,
                     task_id: int | None = None) -> MemoryItem:
    """Manual entry point (e.g. an initial project brief) — the API's POST /projects/{id}/memory."""
    item = MemoryItem(project_id=project.id, task_id=task_id, category=category, title=title, content=content)
    session.add(item)
    record_event(session, project.id, "memory.captured", task_id, category=category.value, title=title)
    session.commit()
    return item
