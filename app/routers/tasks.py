from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import current_user
from app.db import get_session
from app.errors import AppError, not_found, refused
from app.events import record_event
from app.models import Task, TaskDependency, TaskStatus, User
from app.pause import check_task_move, commit_unless_paused
from app.routers.projects import load_project
from app.schemas import TaskCreate, TaskOut, TaskTransition
from app.state_machine import DONE_STATUSES, START_STATUSES, TransitionError

router = APIRouter(prefix="/projects/{project_id}/tasks", tags=["tasks"])


def load_task(session: Session, project_id: int, task_id: int, me: User) -> Task:
    """The task, if its project is `me`'s (checked here, so no route can forget it)."""
    load_project(session, project_id, me)
    task = session.get(Task, task_id)
    if not task or task.project_id != project_id:
        raise not_found("task")
    return task


def dependency_statuses(session: Session, task: Task) -> list[TaskStatus]:
    if not task.depends_on:
        return []
    return list(session.scalars(select(Task.status).where(Task.id.in_(task.depends_on))))


@router.post("", response_model=TaskOut, status_code=201)
def create_task(project_id: int, body: TaskCreate, session: Session = Depends(get_session), me: User = Depends(current_user)):
    load_project(session, project_id, me)
    dep_ids = set(body.depends_on)
    if dep_ids:
        found = set(session.scalars(select(Task.id).where(Task.id.in_(dep_ids), Task.project_id == project_id)))
        if missing := dep_ids - found:
            raise AppError(422, "unknown_dependencies", f"Unknown dependencies in this project: {sorted(missing)}",
                           task_ids=sorted(missing))
    # A new task can only point at existing tasks, so it can never close a cycle;
    # the DAG invariant (§22) holds by construction.
    task = Task(project_id=project_id, **body.model_dump(exclude={"depends_on"}))
    task.dependencies = [TaskDependency(depends_on_id=d) for d in sorted(dep_ids)]
    session.add(task)
    session.flush()
    record_event(session, project_id, "task.created", task.id, owner=task.owner, depends_on=task.depends_on)
    session.commit()
    return task


@router.get("", response_model=list[TaskOut])
def list_tasks(project_id: int, status: TaskStatus | None = None, session: Session = Depends(get_session), me: User = Depends(current_user)):
    load_project(session, project_id, me)
    query = select(Task).where(Task.project_id == project_id).order_by(Task.priority.desc(), Task.id)
    if status is not None:
        query = query.where(Task.status == status)
    return session.scalars(query).all()


@router.get("/runnable", response_model=list[TaskOut])
def runnable_tasks(project_id: int, session: Session = Depends(get_session), me: User = Depends(current_user)):
    """CREATED tasks whose dependencies are all done — these can run in parallel (§33)."""
    load_project(session, project_id, me)
    tasks = session.scalars(
        select(Task)
        .where(Task.project_id == project_id, Task.status == TaskStatus.CREATED)
        .order_by(Task.priority.desc(), Task.id)
    ).all()
    return [t for t in tasks if all(s in DONE_STATUSES for s in dependency_statuses(session, t))]


@router.get("/{task_id}", response_model=TaskOut)
def get_task(project_id: int, task_id: int, session: Session = Depends(get_session), me: User = Depends(current_user)):
    return load_task(session, project_id, task_id, me)


@router.post("/{task_id}/transition", response_model=TaskOut)
def transition_task(project_id: int, task_id: int, body: TaskTransition, session: Session = Depends(get_session), me: User = Depends(current_user)):
    load_project(session, project_id, me)
    task = load_task(session, project_id, task_id, me)
    try:
        check_task_move(session, task, body.status, dependency_statuses=dependency_statuses(session, task))
    except TransitionError as e:
        raise refused(e) from e

    previous = task.status
    if previous is TaskStatus.FAILED and body.status is TaskStatus.READY:
        task.retries += 1
    if body.output is not None:
        task.output = body.output
    task.status = body.status
    record_event(
        session,
        project_id,
        "task.status_changed",
        task.id,
        **{"from": previous.value, "to": body.status.value, "reason": body.reason},
    )
    if body.status in START_STATUSES:  # the owner may have paused since check_task_move read it
        commit_unless_paused(session, project_id, "transition", task.id, to=body.status.value)
    else:
        session.commit()
    return task
