from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import settings_layers
from app.db import get_session
from app.models import Project, SettingsLayer, SettingsScope, Task, Workspace
from app.schemas import SettingsLayerIn, SettingsLayerOut, WorkspaceCreate, WorkspaceOut

router = APIRouter(tags=["settings"])

SCOPE_MODELS = {SettingsScope.WORKSPACE: Workspace, SettingsScope.PROJECT: Project, SettingsScope.TASK: Task}
MAX_REPORTED_PROBLEMS = 10


@router.post("/workspaces", response_model=WorkspaceOut, status_code=201)
def create_workspace(body: WorkspaceCreate, session: Session = Depends(get_session)):
    workspace = Workspace(name=body.name)
    session.add(workspace)
    session.commit()
    return workspace


@router.get("/workspaces", response_model=list[WorkspaceOut])
def list_workspaces(session: Session = Depends(get_session)):
    return session.scalars(select(Workspace).order_by(Workspace.name)).all()


@router.get("/settings/resolved")
def resolved_settings(
    workspace_id: int | None = None,
    project_id: int | None = None,
    task_id: int | None = None,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return settings_layers.resolve(session, workspace_id=workspace_id, project_id=project_id, task_id=task_id)


def _check_scope(session: Session, scope: SettingsScope, scope_id: int) -> None:
    if scope is SettingsScope.GLOBAL:
        if scope_id != 0:
            raise HTTPException(422, "The global layer uses scope_id 0")
    elif not session.get(SCOPE_MODELS[scope], scope_id):
        raise HTTPException(404, f"{scope.value} {scope_id} not found")


@router.get("/settings/{scope}/{scope_id}", response_model=SettingsLayerOut)
def get_settings_layer(scope: SettingsScope, scope_id: int, session: Session = Depends(get_session)):
    _check_scope(session, scope, scope_id)
    layer = settings_layers.get_layer(session, scope, scope_id)
    return SettingsLayerOut(scope=scope, scope_id=scope_id, values=layer.values if layer else {})


def _describe_invalid(error: ValidationError) -> str:
    """One readable line: the Settings page shows `detail` as-is (web/app.js api())."""
    problems = []
    for err in error.errors(include_url=False):
        loc = [str(part) for part in err["loc"]]
        if err["type"] == "extra_forbidden":
            message = "unknown setting"
        elif loc and loc[-1] == "[key]":  # a dict key failed, e.g. an unknown role under "models"
            loc.pop()
            message = f"unknown key; {err['msg']}"
        else:
            # model_type's own text names the internal class ("... or instance of BudgetLayer").
            message = "Input should be a valid dictionary" if err["type"] == "model_type" else err["msg"]
            if isinstance(err["input"], (str, int, float)):
                shown = repr(err["input"])
                message += f" (got {shown if len(shown) <= 40 else shown[:37] + '...'})"
        problems.append(f"{'.'.join(loc)}: {message}")
    if (more := len(problems) - MAX_REPORTED_PROBLEMS) > 0:
        problems = problems[:MAX_REPORTED_PROBLEMS] + [f"and {more} more"]
    return "Invalid settings: " + "; ".join(problems)


@router.put("/settings/{scope}/{scope_id}", response_model=SettingsLayerOut)
def put_settings_layer(
    scope: SettingsScope,
    scope_id: int,
    values: dict[str, Any] = Body(...),
    session: Session = Depends(get_session),
):
    """Replace this layer's overrides. Send only the keys this scope should change (leave a key out
    to inherit it); every value is checked against SettingsLayerIn, then stored exactly as sent."""
    _check_scope(session, scope, scope_id)
    try:
        SettingsLayerIn.model_validate(values)
    except ValidationError as e:
        raise HTTPException(422, _describe_invalid(e)) from e
    layer = settings_layers.get_layer(session, scope, scope_id) or SettingsLayer(scope=scope, scope_id=scope_id)
    layer.values = values
    session.add(layer)
    session.commit()
    return SettingsLayerOut(scope=scope, scope_id=scope_id, values=layer.values)
