"""The pause policy, applied (owner decision d16; the rules are in app/state_machine.py).

One check, run at every entry point (app/manager.py::ensure_active) and again as a backstop where the
paused work would happen — before every model call (Gateway.call, which can't import app.manager) and in
every task move (check_task_move; a test keeps every status write in app/ behind it) — so a path that
misses the first still spends nothing and starts nothing. A pause that lands after a move was checked is
caught as the move commits (commit_unless_paused; work already past a model call uses lock_project).

The project row is the per-project queue for everything that must not interleave with a pause: a move's commit,
a completion's check, a plan decision, a paid call's budget reservation (app/gateway/service.py). They all lock
it the same way (_paused with lock=True); tests/test_pause_task_moves.py and tests/test_budget_holds.py pin the
PostgreSQL SQL.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.events import record_event
from app.models import Project, Task, TaskStatus
from app.state_machine import START_STATUSES, ProjectPaused, check_project_active, check_task_transition


def _paused(session: Session, project_id: int, *, lock: bool = False) -> bool:
    """`paused` read fresh, not from a Project the caller loaded: the owner's pause commits in another
    session. What this session has flushed counts, so a resume flushed before its promotions reads active.

    `lock` locks the project row, flushes the caller's work and reads under both, so a pause can't land between
    this read and the caller's commit. Every locker takes the project row before any other row it writes:
    PostgreSQL's row locks would otherwise deadlock one that wrote its task rows first (a reject deleting a task)
    against one that holds the project row and then wants those rows (a completion promoting that task). The lock
    is FOR NO KEY UPDATE: it conflicts with the pause's UPDATE and with the other lockers, but not with the KEY
    SHARE that foreign keys to the project take (a flushed event's, say), where FOR UPDATE could deadlock two
    completions in one project. SQLite renders no lock: its one write lock is taken by the flush, and the read
    after the flush is the one that counts."""
    query = select(Project.paused).where(Project.id == project_id)
    if lock:
        query = query.with_for_update(key_share=True)
        session.scalar(query)  # PostgreSQL: the row lock, before this session writes any row
        session.flush()  # SQLite: the write lock
    return bool(session.scalar(query))  # no such project: not paused


def lock_project(session: Session, project_id: int) -> bool:
    """Lock the project row, flush the caller's work (see _paused) and return `paused`, for work that is past
    its entry check and must not interleave with a pause or another locker until the caller commits."""
    return _paused(session, project_id, lock=True)


def _refuse(session: Session, project_id: int, action: str, task_id: int | None, **detail: Any) -> None:
    """Record a `pause.blocked` event so the owner can see what the pause stopped. A refusal changes nothing
    else: the caller's uncommitted work (which may hold a READY/RUNNING move) is rolled back first."""
    session.rollback()
    if task_id is not None and session.scalar(select(Task.id).where(Task.id == task_id)) is None:
        task_id = None  # the task was part of the rolled-back work (or deleted meanwhile)
    record_event(session, project_id, "pause.blocked", task_id, action=action, **detail)
    session.commit()


def refuse_if_paused(
    session: Session, project_id: int, action: str, task_id: int | None = None, *, lock: bool = False, **detail: Any
) -> None:
    """Raise ProjectPaused for a paused project, after recording the refusal."""
    try:
        check_project_active(_paused(session, project_id, lock=lock))
    except ProjectPaused:
        _refuse(session, project_id, action, task_id, **detail)
        raise


def commit_unless_paused(
    session: Session, project_id: int, action: str, task_id: int | None = None, **detail: Any
) -> None:
    """Commit the caller's READY/RUNNING moves unless the owner paused after they were checked; the lock is
    held only from this read to the commit, never across a model call."""
    refuse_if_paused(session, project_id, action, task_id, lock=True, **detail)
    session.commit()


def check_task_move(
    session: Session, task: Task, target: TaskStatus, *,
    dependency_statuses: list[TaskStatus], reason: str | None = None,
) -> None:
    """check_task_transition with `paused` read fresh, for every task move: a paused project's move to
    READY/RUNNING is refused even on a path that skipped ensure_active, and recorded like refuse_if_paused's
    refusal (action "transition"). A pause landing after this read is caught as the move commits."""
    project_id, task_id = task.project_id, task.id  # read before a refusal's rollback expires `task`
    try:
        check_task_transition(
            task.status, target, dependency_statuses=dependency_statuses,
            retries=task.retries, max_retries=task.max_retries,
            paused=target in START_STATUSES and _paused(session, project_id),  # no other move reads it
        )
    except ProjectPaused:
        why = {"reason": reason} if reason else {}
        _refuse(session, project_id, "transition", task_id, to=target.value, **why)
        raise
