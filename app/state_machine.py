"""Task and project state machines (docs/ARCHITECTURE.md §23, §40, §46).

Pure functions only — the routers apply the transition and record a RunEvent.
"""

from app.models import ProjectStage, TaskStatus

S = TaskStatus

TASK_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    S.CREATED: {S.READY, S.CANCELLED},
    S.READY: {S.RUNNING, S.CANCELLED},
    S.RUNNING: {S.WAITING, S.COMPLETED, S.FAILED},
    S.WAITING: {S.RUNNING, S.FAILED, S.CANCELLED},
    # COMPLETED -> READY is the reviewer's CHANGES_REQUIRED loop (§21).
    S.COMPLETED: {S.REVIEWED, S.READY},
    # FAILED -> READY is a retry (bounded by max_retries); otherwise escalate (§46).
    S.FAILED: {S.READY, S.ESCALATED},
    S.ESCALATED: {S.READY, S.CANCELLED},
    S.REVIEWED: set(),
    S.CANCELLED: set(),
}

# A dependency is satisfied once its task produced a result.
DONE_STATUSES = {S.COMPLETED, S.REVIEWED}


class TransitionError(ValueError):
    pass


def check_task_transition(
    current: TaskStatus,
    target: TaskStatus,
    *,
    dependency_statuses: list[TaskStatus],
    retries: int,
    max_retries: int,
) -> None:
    if target not in TASK_TRANSITIONS[current]:
        raise TransitionError(f"Cannot move task from {current.value} to {target.value}")
    if target is S.READY and current is S.CREATED:
        blocked = [s for s in dependency_statuses if s not in DONE_STATUSES]
        if blocked:
            raise TransitionError("Task has unfinished dependencies")
    if current is S.FAILED and target is S.READY and retries >= max_retries:
        raise TransitionError(f"Retry limit reached ({max_retries}); escalate instead")


PROJECT_STAGES = list(ProjectStage)


def check_project_advance(current: ProjectStage, target: ProjectStage) -> None:
    """Stages move forward one step at a time; ITERATION loops back to DISCOVERY."""
    if current is ProjectStage.ITERATION and target is ProjectStage.DISCOVERY:
        return
    idx = PROJECT_STAGES.index(current)
    if idx + 1 >= len(PROJECT_STAGES) or PROJECT_STAGES[idx + 1] is not target:
        raise TransitionError(f"Cannot move project from {current.value} to {target.value}")
