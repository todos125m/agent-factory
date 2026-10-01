from typing import Any, Callable, TypeVar

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import chat, manager
from app.db import get_session
from app.gateway.providers import ProviderError
from app.gateway.service import BudgetExceeded, Gateway
from app.manager import ManagerError
from app.routers.projects import load_project
from app.routers.tasks import load_task
from app.schemas import ChatMessageOut, ChatTurnOut, TaskOut, TaskRunResultOut
from app.state_machine import ProjectPaused, TransitionError

router = APIRouter(prefix="/projects/{project_id}", tags=["manager"])

T = TypeVar("T")


def get_gateway(session: Session = Depends(get_session)) -> Gateway:
    return Gateway(session)


def _run(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    try:
        return fn(*args, **kwargs)
    except ManagerError as e:
        raise HTTPException(422, str(e)) from e
    except BudgetExceeded as e:
        raise HTTPException(402, str(e)) from e
    except ProviderError as e:
        raise HTTPException(502, str(e)) from e
    except (TransitionError, ProjectPaused) as e:  # every route here is refused while paused
        raise HTTPException(409, str(e)) from e


class PlanReject(BaseModel):
    feedback: str


class DecisionIn(BaseModel):
    option: int
    note: str | None = None


class ChatIn(BaseModel):
    text: str


@router.post("/plan")
def create_plan(project_id: int, session: Session = Depends(get_session), gateway: Gateway = Depends(get_gateway)):
    project = load_project(session, project_id)
    return _run(manager.create_plan, session, gateway, project)


@router.post("/plan/approve")
def approve_plan(project_id: int, session: Session = Depends(get_session)):
    project = load_project(session, project_id)
    return _run(manager.approve_plan, session, project)


@router.post("/plan/reject")
def reject_plan(
    project_id: int, body: PlanReject, session: Session = Depends(get_session), gateway: Gateway = Depends(get_gateway)
):
    project = load_project(session, project_id)
    return _run(manager.reject_plan, session, gateway, project, body.feedback)


@router.post("/tasks/{tid}/checkpoint")
def create_checkpoint(
    project_id: int, tid: int, session: Session = Depends(get_session), gateway: Gateway = Depends(get_gateway)
):
    project = load_project(session, project_id)
    task = load_task(session, project_id, tid)
    return _run(manager.create_checkpoint, session, gateway, project, task)


@router.post("/tasks/{tid}/decide", response_model=TaskOut)
def decide(project_id: int, tid: int, body: DecisionIn, session: Session = Depends(get_session)):
    project = load_project(session, project_id)
    task = load_task(session, project_id, tid)
    _run(manager.decide, session, project, task, body.option, body.note)
    return task


@router.post("/tasks/{tid}/run", response_model=TaskRunResultOut)
def run_task(
    project_id: int, tid: int, session: Session = Depends(get_session), gateway: Gateway = Depends(get_gateway)
):
    project = load_project(session, project_id)
    task = load_task(session, project_id, tid)
    return _run(manager.run_task, session, gateway, project, task)


@router.post("/chat", response_model=ChatTurnOut)
def send_chat(
    project_id: int, body: ChatIn, session: Session = Depends(get_session), gateway: Gateway = Depends(get_gateway)
):
    project = load_project(session, project_id)
    return _run(chat.send_message, session, gateway, project, body.text)


@router.get("/chat", response_model=list[ChatMessageOut])
def list_chat(project_id: int, session: Session = Depends(get_session)):
    project = load_project(session, project_id)
    return chat.list_messages(session, project)
