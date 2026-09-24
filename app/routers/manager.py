from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import manager
from app.db import get_session
from app.gateway.providers import ProviderError
from app.gateway.service import BudgetExceeded, Gateway
from app.manager import ManagerError
from app.routers.projects import load_project
from app.routers.tasks import load_task
from app.schemas import TaskOut
from app.state_machine import TransitionError

router = APIRouter(prefix="/projects/{project_id}", tags=["manager"])


def get_gateway(session: Session = Depends(get_session)) -> Gateway:
    return Gateway(session)


def _run(fn: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> dict[str, Any]:
    try:
        return fn(*args, **kwargs)
    except ManagerError as e:
        raise HTTPException(422, str(e)) from e
    except BudgetExceeded as e:
        raise HTTPException(402, str(e)) from e
    except ProviderError as e:
        raise HTTPException(502, str(e)) from e


class PlanReject(BaseModel):
    feedback: str


class DecisionIn(BaseModel):
    option: int
    note: str | None = None


@router.post("/plan")
def create_plan(project_id: int, session: Session = Depends(get_session), gateway: Gateway = Depends(get_gateway)):
    project = load_project(session, project_id)
    return _run(manager.create_plan, session, gateway, project)


@router.post("/plan/approve")
def approve_plan(project_id: int, session: Session = Depends(get_session)):
    project = load_project(session, project_id)
    return manager.approve_plan(session, project)


@router.post("/plan/reject")
def reject_plan(
    project_id: int, body: PlanReject, session: Session = Depends(get_session), gateway: Gateway = Depends(get_gateway)
):
    project = load_project(session, project_id)
    manager.reject_plan(session, project, body.feedback)
    return _run(manager.create_plan, session, gateway, project, feedback=body.feedback)


@router.post("/tasks/{tid}/checkpoint")
def create_checkpoint(
    project_id: int, tid: int, session: Session = Depends(get_session), gateway: Gateway = Depends(get_gateway)
):
    project = load_project(session, project_id)
    task = load_task(session, project_id, tid)
    return _run(manager.create_checkpoint, session, gateway, project, task)


@router.post("/tasks/{tid}/decide", response_model=TaskOut)
def decide(project_id: int, tid: int, body: DecisionIn, session: Session = Depends(get_session)):
    load_project(session, project_id)
    task = load_task(session, project_id, tid)
    try:
        manager.decide(session, task, body.option, body.note)
    except TransitionError as e:
        raise HTTPException(409, str(e)) from e
    return task
