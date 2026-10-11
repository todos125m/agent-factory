from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import manager
from app import memory as memory_module
from app import auth, settings_layers
from app.auth import current_user
from app.db import get_session
from app.errors import not_found, refused
from app.events import record_event
from app.models import LearningTrace, MemoryCategory, MemoryItem, ModelCall, Project, RunEvent, Task, User
from app.schemas import (
    LearningTraceOut, MemoryItemCreate, MemoryItemOut, ModelCallOut, ProjectCreate, ProjectOut,
    ProjectPauseUpdate, ProjectStageUpdate, RunEventOut, UsageOut,
)
from app.state_machine import TransitionError, check_project_advance

router = APIRouter(prefix="/projects", tags=["projects"])


def load_project(session: Session, project_id: int, me: User) -> Project:
    """The project if it is `me`'s; another account's is the same 404 as a missing one (app/auth.py)."""
    return auth.own_project(session, project_id, me)


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectCreate, session: Session = Depends(get_session), me: User = Depends(current_user)):
    # A project is made by and for the signed-in account: `owner_id` may only name it (older clients send it).
    if body.owner_id is not None and body.owner_id != me.id:
        raise auth.forbidden("owner_must_be_you")
    if body.workspace_id is not None:
        auth.own_workspace(session, body.workspace_id, me)
    project = Project(**{**body.model_dump(), "owner_id": me.id})
    session.add(project)
    session.flush()
    record_event(session, project.id, "project.created", mode=project.mode.value, stage=project.stage.value)
    session.commit()
    return project


@router.get("", response_model=list[ProjectOut])
def list_projects(owner_id: int | None = None, session: Session = Depends(get_session), me: User = Depends(current_user)):
    """The signed-in account's projects (`owner_id`, if sent, must be that account)."""
    if owner_id is not None and owner_id != me.id:
        raise auth.forbidden("owner_must_be_you")
    return session.scalars(select(Project).where(Project.owner_id == me.id).order_by(Project.id)).all()


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, session: Session = Depends(get_session), me: User = Depends(current_user)):
    return load_project(session, project_id, me)


@router.post("/{project_id}/stage", response_model=ProjectOut)
def advance_stage(project_id: int, body: ProjectStageUpdate, session: Session = Depends(get_session), me: User = Depends(current_user)):
    project = load_project(session, project_id, me)
    try:
        manager.ensure_active(session, project, "stage", to=body.stage.value)
        check_project_advance(project.stage, body.stage)
    except TransitionError as e:
        raise refused(e) from e
    record_event(session, project.id, "project.stage_changed", **{"from": project.stage.value, "to": body.stage.value})
    project.stage = body.stage
    session.commit()
    return project


@router.post("/{project_id}/pause", response_model=ProjectOut)
def set_paused(project_id: int, body: ProjectPauseUpdate, session: Session = Depends(get_session), me: User = Depends(current_user)):
    project = load_project(session, project_id, me)
    if project.paused != body.paused:
        project.paused = body.paused
        record_event(session, project.id, "project.paused" if body.paused else "project.resumed")
        if not body.paused:
            manager.release_withheld(session, project)
        session.commit()
    return project


@router.get("/{project_id}/events", response_model=list[RunEventOut])
def list_events(project_id: int, session: Session = Depends(get_session), me: User = Depends(current_user)):
    load_project(session, project_id, me)
    return session.scalars(select(RunEvent).where(RunEvent.project_id == project_id).order_by(RunEvent.id)).all()


@router.get("/{project_id}/usage", response_model=UsageOut)
def project_usage(project_id: int, session: Session = Depends(get_session), me: User = Depends(current_user)):
    load_project(session, project_id, me)
    row = session.execute(
        select(
            func.count(ModelCall.id),
            func.coalesce(func.sum(ModelCall.input_tokens), 0),
            func.coalesce(func.sum(ModelCall.output_tokens), 0),
            func.coalesce(func.sum(ModelCall.cache_read_tokens), 0),
            func.coalesce(func.sum(ModelCall.cost_usd), 0.0),
        ).where(ModelCall.project_id == project_id)
    ).one()
    budget = settings_layers.resolve(session, project_id=project_id)["budget"]["project_usd"]
    return UsageOut(
        calls=row[0], input_tokens=row[1], output_tokens=row[2], cache_read_tokens=row[3],
        cost_usd=round(float(row[4]), 6), budget_usd=float(budget),
    )


@router.get("/{project_id}/model_calls", response_model=list[ModelCallOut])
def list_model_calls(project_id: int, session: Session = Depends(get_session), me: User = Depends(current_user)):
    load_project(session, project_id, me)
    return session.scalars(
        select(ModelCall).where(ModelCall.project_id == project_id).order_by(ModelCall.id.desc())
    ).all()


@router.get("/{project_id}/memory", response_model=list[MemoryItemOut])
def list_memory(project_id: int, category: MemoryCategory | None = None, session: Session = Depends(get_session), me: User = Depends(current_user)):
    load_project(session, project_id, me)
    query = select(MemoryItem).where(MemoryItem.project_id == project_id).order_by(MemoryItem.id.desc())
    if category is not None:
        query = query.where(MemoryItem.category == category)
    return session.scalars(query).all()


@router.post("/{project_id}/memory", response_model=MemoryItemOut, status_code=201)
def create_memory(project_id: int, body: MemoryItemCreate, session: Session = Depends(get_session), me: User = Depends(current_user)):
    project = load_project(session, project_id, me)
    if body.task_id is not None:
        task = session.get(Task, body.task_id)
        if not task or task.project_id != project_id:
            raise not_found("task", "Task not found in this project")
    return memory_module.add_memory_item(
        session, project, body.category, body.title, body.content, task_id=body.task_id
    )


@router.get("/{project_id}/learning", response_model=list[LearningTraceOut])
def list_learning(project_id: int, session: Session = Depends(get_session), me: User = Depends(current_user)):
    load_project(session, project_id, me)
    return session.scalars(
        select(LearningTrace).where(LearningTrace.project_id == project_id).order_by(LearningTrace.id.desc())
    ).all()
