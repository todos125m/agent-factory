from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import settings_layers
from app.db import get_session
from app.events import record_event
from app.models import ModelCall, Project, RunEvent, User, Workspace
from app.schemas import ProjectCreate, ProjectOut, ProjectPauseUpdate, ProjectStageUpdate, RunEventOut, UsageOut
from app.state_machine import TransitionError, check_project_advance

router = APIRouter(prefix="/projects", tags=["projects"])


def load_project(session: Session, project_id: int) -> Project:
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return project


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectCreate, session: Session = Depends(get_session)):
    if not session.get(User, body.owner_id):
        raise HTTPException(404, "Owner not found")
    if body.workspace_id is not None and not session.get(Workspace, body.workspace_id):
        raise HTTPException(404, "Workspace not found")
    project = Project(**body.model_dump())
    session.add(project)
    session.flush()
    record_event(session, project.id, "project.created", mode=project.mode.value, stage=project.stage.value)
    session.commit()
    return project


@router.get("", response_model=list[ProjectOut])
def list_projects(owner_id: int | None = None, session: Session = Depends(get_session)):
    query = select(Project).order_by(Project.id)
    if owner_id is not None:
        query = query.where(Project.owner_id == owner_id)
    return session.scalars(query).all()


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, session: Session = Depends(get_session)):
    return load_project(session, project_id)


@router.post("/{project_id}/stage", response_model=ProjectOut)
def advance_stage(project_id: int, body: ProjectStageUpdate, session: Session = Depends(get_session)):
    project = load_project(session, project_id)
    if project.paused:
        raise HTTPException(409, "Project is paused")
    try:
        check_project_advance(project.stage, body.stage)
    except TransitionError as e:
        raise HTTPException(409, str(e)) from e
    record_event(session, project.id, "project.stage_changed", **{"from": project.stage.value, "to": body.stage.value})
    project.stage = body.stage
    session.commit()
    return project


@router.post("/{project_id}/pause", response_model=ProjectOut)
def set_paused(project_id: int, body: ProjectPauseUpdate, session: Session = Depends(get_session)):
    project = load_project(session, project_id)
    if project.paused != body.paused:
        project.paused = body.paused
        record_event(session, project.id, "project.paused" if body.paused else "project.resumed")
        session.commit()
    return project


@router.get("/{project_id}/events", response_model=list[RunEventOut])
def list_events(project_id: int, session: Session = Depends(get_session)):
    load_project(session, project_id)
    return session.scalars(select(RunEvent).where(RunEvent.project_id == project_id).order_by(RunEvent.id)).all()


@router.get("/{project_id}/usage", response_model=UsageOut)
def project_usage(project_id: int, session: Session = Depends(get_session)):
    load_project(session, project_id)
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
