from typing import Any

from fastapi import APIRouter, Body, Depends
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth, settings_layers
from app.auth import current_user
from app.db import get_session
from app.errors import AppError, not_found
from app.models import SettingsLayer, SettingsScope, Task, User, Workspace
from app.schemas import SettingsLayerIn, SettingsLayerOut, WorkspaceCreate, WorkspaceOut

router = APIRouter(tags=["settings"])

MAX_REPORTED_PROBLEMS = 10


@router.post("/workspaces", response_model=WorkspaceOut, status_code=201)
def create_workspace(body: WorkspaceCreate, session: Session = Depends(get_session), me: User = Depends(current_user)):
    workspace = Workspace(name=body.name, owner_id=me.id)
    session.add(workspace)
    session.commit()
    return workspace


@router.get("/workspaces", response_model=list[WorkspaceOut])
def list_workspaces(session: Session = Depends(get_session), me: User = Depends(current_user)):
    return session.scalars(select(Workspace).where(Workspace.owner_id == me.id).order_by(Workspace.name)).all()


@router.get("/settings/resolved")
def resolved_settings(
    workspace_id: int | None = None,
    project_id: int | None = None,
    task_id: int | None = None,
    session: Session = Depends(get_session), me: User = Depends(current_user),
) -> dict[str, Any]:
    """Effective settings for the signed-in account's own workspace / project / task (each id given is checked)."""
    if workspace_id is not None:
        auth.own_workspace(session, workspace_id, me)
    if project_id is not None:
        auth.own_project(session, project_id, me)
    if task_id is not None:
        _own_task(session, task_id, me)
    return settings_layers.resolve(session, workspace_id=workspace_id, project_id=project_id, task_id=task_id)


def _own_task(session: Session, task_id: int, me: User) -> Task:
    task = session.get(Task, task_id)
    if task is None or task.project.owner_id != me.id:
        raise not_found("task")
    return task


def _check_scope(session: Session, scope: SettingsScope, scope_id: int, me: User, *, writing: bool = False) -> None:
    """Every account reads the global layer (what it inherits) and only the admin changes it (it is every account's);
    a workspace, project or task layer is its owner's alone — another account's is the same 404 as a missing one."""
    if scope is SettingsScope.GLOBAL:
        if scope_id != 0:
            raise AppError(422, "invalid_request", "The global layer uses scope_id 0")
        if writing and not me.is_admin:
            raise auth.forbidden("admin_only")
    elif scope is SettingsScope.WORKSPACE:
        auth.own_workspace(session, scope_id, me)
    elif scope is SettingsScope.PROJECT:
        auth.own_project(session, scope_id, me)
    else:
        _own_task(session, scope_id, me)


@router.get("/settings/{scope}/{scope_id}", response_model=SettingsLayerOut)
def get_settings_layer(scope: SettingsScope, scope_id: int, session: Session = Depends(get_session), me: User = Depends(current_user)):
    _check_scope(session, scope, scope_id, me)
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
    session: Session = Depends(get_session), me: User = Depends(current_user),
):
    """Replace this layer's overrides. Send only the keys this scope should change (leave a key out
    to inherit it); every value is checked against SettingsLayerIn, then stored exactly as sent."""
    _check_scope(session, scope, scope_id, me, writing=True)
    try:
        SettingsLayerIn.model_validate(values)
    except ValidationError as e:
        raise AppError(422, "settings_invalid", _describe_invalid(e)) from e
    layer = settings_layers.get_layer(session, scope, scope_id) or SettingsLayer(scope=scope, scope_id=scope_id)
    layer.values = values
    session.add(layer)
    session.commit()
    return SettingsLayerOut(scope=scope, scope_id=scope_id, values=layer.values)
