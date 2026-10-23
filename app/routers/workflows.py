"""Workflows (app/workflow.py): agents connected on a canvas — save a version, approve exactly it, run it as tasks."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy.orm import Session

from app import workflow
from app.auth import current_user
from app.db import get_session
from app.models import User, Workflow
from app.storable import Int32, StorableIn, limit

router = APIRouter(prefix="/workflows", tags=["workflows"])

RequestId = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class WorkflowCreate(StorableIn):
    name: str = Field(min_length=1, max_length=limit(Workflow, "name"))
    request_id: str = RequestId


class Change(StorableIn):
    expected_revision: Int32
    request_id: str = RequestId


class WorkflowSave(workflow.GraphIn):
    expected_revision: Int32
    request_id: str = RequestId


class WorkflowApprove(Change):
    version: Int32


class WorkflowRun(StorableIn):
    version: Int32
    project_id: Int32
    request_id: str = RequestId


class Position(StorableIn):
    x: float = Field(ge=0, le=100_000)
    y: float = Field(ge=0, le=100_000)
    model_config = {"extra": "forbid"}


class Layout(StorableIn):
    positions: dict[Annotated[str, Field(max_length=40)], Position] = Field(max_length=workflow.MAX_NODES)


@router.get("")
def list_workflows(db: Session = Depends(get_session), me: User = Depends(current_user)) -> list[dict[str, Any]]:
    return workflow.listing(db, me)


@router.post("", status_code=201)
def create_workflow(body: WorkflowCreate, db: Session = Depends(get_session), me: User = Depends(current_user)):
    w, applied = workflow.create(db, me, name=body.name.strip(), request_id=body.request_id)
    return {**workflow.view(db, w, me), "duplicate": not applied}


@router.get("/{workflow_id}")
def get_workflow(workflow_id: int, db: Session = Depends(get_session), me: User = Depends(current_user)):
    return workflow.view(db, workflow.load(db, workflow_id, me), me)


@router.put("/{workflow_id}/draft")
def save_workflow(workflow_id: int, body: WorkflowSave, db: Session = Depends(get_session),
                  me: User = Depends(current_user)):
    """Save a new draft version: nothing runs, nothing is approved. Same graph again: no new version."""
    w, applied = workflow.save(db, me, workflow_id, body, expected_revision=body.expected_revision,
                               request_id=body.request_id)
    return {**workflow.view(db, workflow.load(db, w.id, me), me), "duplicate": not applied}


@router.put("/{workflow_id}/layout")
def save_layout(workflow_id: int, body: Layout, db: Session = Depends(get_session), me: User = Depends(current_user)):
    """Where the nodes sit. Display only: no version, no approval changes."""
    positions = {k: {"x": p.x, "y": p.y} for k, p in body.positions.items()}
    return workflow.view(db, workflow.set_layout(db, me, workflow_id, positions), me)


@router.post("/{workflow_id}/approve")
def approve_workflow(workflow_id: int, body: WorkflowApprove, db: Session = Depends(get_session),
                     me: User = Depends(current_user)):
    w, applied = workflow.approve(db, me, workflow_id, version=body.version, expected_revision=body.expected_revision,
                                  request_id=body.request_id)
    return {**workflow.view(db, workflow.load(db, w.id, me), me), "duplicate": not applied}


@router.post("/{workflow_id}/run", status_code=201)
def run_workflow(workflow_id: int, body: WorkflowRun, db: Session = Depends(get_session),
                 me: User = Depends(current_user)):
    """The approved version becomes the project's tasks (a plan awaiting your approval); nothing executes yet."""
    result, _ = workflow.run(db, me, workflow_id, version=body.version, project_id=body.project_id,
                             request_id=body.request_id)
    return result
