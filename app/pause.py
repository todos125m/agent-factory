"""The pause policy, applied (owner decision d16; the rule is state_machine.check_project_active).

One check, run at every entry point (app/manager.py::ensure_active) and again before every model call
(Gateway.call, which can't import app.manager), so a path that misses the first still spends nothing.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.events import record_event
from app.models import Project, Task
from app.state_machine import ProjectPaused, check_project_active


def refuse_if_paused(session: Session, project_id: int, action: str, task_id: int | None = None, **detail: Any) -> None:
    """Raise ProjectPaused for a paused project, after recording a `pause.blocked` event so the owner can
    see what the pause stopped. `paused` is read fresh, not from a Project the caller loaded: the owner's
    pause commits in another session. A refusal changes nothing else: the caller's uncommitted work (which
    may hold a READY/RUNNING move) is rolled back first."""
    paused = session.scalar(select(Project.paused).where(Project.id == project_id))
    try:
        check_project_active(bool(paused))  # no such project: nothing to pause
    except ProjectPaused:
        session.rollback()
        if task_id is not None and session.scalar(select(Task.id).where(Task.id == task_id)) is None:
            task_id = None  # the task was part of the rolled-back work (or deleted meanwhile)
        record_event(session, project_id, "pause.blocked", task_id, action=action, **detail)
        session.commit()
        raise
