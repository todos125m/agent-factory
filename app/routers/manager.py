from typing import Any, Callable, TypeVar

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import chat, manager, runtime, storable
from app.auth import current_user
from app.models import Run, User
from app.db import get_session
from app.errors import AppError, not_found, refused
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
        raise AppError(422, "request_refused", str(e)) from e
    except BudgetExceeded as e:
        raise AppError(402, "budget_exceeded", str(e)) from e
    except ProviderError as e:  # its text can quote a model's: JSON can't carry a lone surrogate
        raise AppError(502, "provider_unavailable", storable.clean(str(e))) from e
    except (TransitionError, ProjectPaused) as e:  # every route here is refused while paused
        raise refused(e) from e


class PlanReject(storable.StorableIn):  # the owner's text, refused before the re-plan's paid call
    feedback: str


class DecisionIn(storable.StorableIn):
    option: int
    note: str | None = None


class ChatIn(BaseModel):
    text: str


@router.post("/plan")
def create_plan(project_id: int, session: Session = Depends(get_session), me: User = Depends(current_user), gateway: Gateway = Depends(get_gateway)):
    project = load_project(session, project_id, me)
    return _run(manager.create_plan, session, gateway, project)


@router.post("/plan/approve")
def approve_plan(project_id: int, session: Session = Depends(get_session), me: User = Depends(current_user)):
    project = load_project(session, project_id, me)
    return _run(manager.approve_plan, session, project)


@router.post("/plan/reject")
def reject_plan(
    project_id: int, body: PlanReject, session: Session = Depends(get_session), me: User = Depends(current_user), gateway: Gateway = Depends(get_gateway)
):
    project = load_project(session, project_id, me)
    return _run(manager.reject_plan, session, gateway, project, body.feedback)


@router.post("/tasks/{tid}/checkpoint")
def create_checkpoint(
    project_id: int, tid: int, session: Session = Depends(get_session), me: User = Depends(current_user), gateway: Gateway = Depends(get_gateway)
):
    project = load_project(session, project_id, me)
    task = load_task(session, project_id, tid, me)
    return _run(manager.create_checkpoint, session, gateway, project, task)


@router.post("/tasks/{tid}/decide", response_model=TaskOut)
def decide(project_id: int, tid: int, body: DecisionIn, session: Session = Depends(get_session), me: User = Depends(current_user)):
    project = load_project(session, project_id, me)
    task = load_task(session, project_id, tid, me)
    _run(manager.decide, session, project, task, body.option, body.note)
    return task


@router.post("/tasks/{tid}/run", response_model=TaskRunResultOut)
def run_task(
    project_id: int, tid: int, session: Session = Depends(get_session), me: User = Depends(current_user), gateway: Gateway = Depends(get_gateway)
):
    project = load_project(session, project_id, me)
    task = load_task(session, project_id, tid, me)
    return _run(manager.run_task, session, gateway, project, task)


@router.post("/tasks/{tid}/unblock", response_model=TaskRunResultOut)
def unblock(project_id: int, tid: int, session: Session = Depends(get_session), me: User = Depends(current_user)):
    """A BLOCKED task once its specialist is registered: CREATED again (READY when its dependencies allow)."""
    project = load_project(session, project_id, me)
    task = load_task(session, project_id, tid, me)
    return _run(manager.unblock, session, project, task)


@router.post("/chat", response_model=ChatTurnOut)
def send_chat(
    project_id: int, body: ChatIn, session: Session = Depends(get_session), me: User = Depends(current_user), gateway: Gateway = Depends(get_gateway)
):
    project = load_project(session, project_id, me)
    return _run(chat.send_message, session, gateway, project, body.text)


@router.get("/chat", response_model=list[ChatMessageOut])
def list_chat(project_id: int, session: Session = Depends(get_session), me: User = Depends(current_user)):
    project = load_project(session, project_id, me)
    return chat.list_messages(session, project)


# ---------- bounded runs (app/runtime.py) ----------


class RespondIn(storable.StorableIn):
    text: str | None = Field(default=None, max_length=4000)
    approve: bool | None = None
    happened: bool | None = None


@router.post("/tasks/{tid}/runs", status_code=201)
def start_run(project_id: int, tid: int, session: Session = Depends(get_session), me: User = Depends(current_user)):
    """Queue a bounded, step-by-step run of a RUNNING task; a worker executes it (poll GET .../runs/{id})."""
    project = load_project(session, project_id, me)
    task = load_task(session, project_id, tid, me)
    manager.ensure_active(session, project, "run", task.id)
    return runtime.view(session, runtime.start(session, project, task, me))


@router.get("/tasks/{tid}/runs")
def list_runs(project_id: int, tid: int, session: Session = Depends(get_session), me: User = Depends(current_user)):
    load_task(session, project_id, tid, me)
    return [runtime.view(session, r) for r in session.scalars(
        select(Run).where(Run.task_id == tid).order_by(Run.id.desc()).limit(10))]


def _load_run(session: Session, project_id: int, tid: int, rid: int, me: User) -> tuple:
    project = load_project(session, project_id, me)
    task = load_task(session, project_id, tid, me)
    run = session.get(Run, rid)
    if run is None or run.task_id != task.id:
        raise not_found("run")
    return project, task, run


@router.get("/tasks/{tid}/runs/{rid}")
def get_run(project_id: int, tid: int, rid: int, session: Session = Depends(get_session),
            me: User = Depends(current_user)):
    return runtime.view(session, _load_run(session, project_id, tid, rid, me)[2])


@router.post("/tasks/{tid}/runs/{rid}/respond")
def respond_run(project_id: int, tid: int, rid: int, body: RespondIn, session: Session = Depends(get_session),
                me: User = Depends(current_user)):
    """The owner's answer to what the run waits on: a question (text), a tool action (approve), an unknown outcome
    (happened). The same run then continues."""
    project, task, run = _load_run(session, project_id, tid, rid, me)
    return runtime.view(session, runtime.respond(session, project, task, run, me, text=body.text,
                                                 approve=body.approve, happened=body.happened))


@router.post("/tasks/{tid}/runs/{rid}/cancel")
def cancel_run(project_id: int, tid: int, rid: int, session: Session = Depends(get_session),
               me: User = Depends(current_user)):
    project, task, run = _load_run(session, project_id, tid, rid, me)
    return runtime.view(session, runtime.cancel(session, project, task, run, me))
