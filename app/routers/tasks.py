from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.events import record_event
from app.models import Task, TaskDependency, TaskStatus
from app.routers.projects import load_project
from app.schemas import TaskCreate, TaskOut, TaskTransition
from app.state_machine import DONE_STATUSES, TransitionError, check_task_transition

router = APIRouter(prefix="/projects/{project_id}/tasks", tags=["tasks"])


def load_task(session: Session, project_id: int, task_id: int) -> Task:
    task = session.get(Task, task_id)
    if not task or task.project_id != project_id:
        raise HTTPException(404, "Task not found")
    return task


def dependency_statuses(session: Session, task: Task) -> list[TaskStatus]:
    if not task.depends_on:
        return []
    return list(session.scalars(select(Task.status).where(Task.id.in_(task.depends_on))))


@router.post("", response_model=TaskOut, status_code=201)
def create_task(project_id: int, body: TaskCreate, session: Session = Depends(get_session)):
    load_project(session, project_id)
    dep_ids = set(body.depends_on)
    if dep_ids:
        found = set(session.scalars(select(Task.id).where(Task.id.in_(dep_ids), Task.project_id == project_id)))
        if missing := dep_ids - found:
            raise HTTPException(422, f"Unknown dependencies in this project: {sorted(missing)}")
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
def list_tasks(project_id: int, status: TaskStatus | None = None, session: Session = Depends(get_session)):
    load_project(session, project_id)
    query = select(Task).where(Task.project_id == project_id).order_by(Task.priority.desc(), Task.id)
    if status is not None:
        query = query.where(Task.status == status)
    return session.scalars(query).all()


@router.get("/runnable", response_model=list[TaskOut])
def runnable_tasks(project_id: int, session: Session = Depends(get_session)):
    """CREATED tasks whose dependencies are all done — these can run in parallel (§33)."""
    load_project(session, project_id)
    tasks = session.scalars(
        select(Task)
        .where(Task.project_id == project_id, Task.status == TaskStatus.CREATED)
        .order_by(Task.priority.desc(), Task.id)
    ).all()
    return [t for t in tasks if all(s in DONE_STATUSES for s in dependency_statuses(session, t))]


@router.get("/{task_id}", response_model=TaskOut)
def get_task(project_id: int, task_id: int, session: Session = Depends(get_session)):
    return load_task(session, project_id, task_id)


@router.post("/{task_id}/transition", response_model=TaskOut)
def transition_task(project_id: int, task_id: int, body: TaskTransition, session: Session = Depends(get_session)):
    project = load_project(session, project_id)
    task = load_task(session, project_id, task_id)
    if project.paused and body.status in {TaskStatus.READY, TaskStatus.RUNNING}:
        raise HTTPException(409, "Project is paused")
    try:
        check_task_transition(
            task.status,
            body.status,
            dependency_statuses=dependency_statuses(session, task),
            retries=task.retries,
            max_retries=task.max_retries,
        )
    except TransitionError as e:
        raise HTTPException(409, str(e)) from e

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
    session.commit()
    return task
