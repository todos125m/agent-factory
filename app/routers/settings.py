from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from app import settings_layers
from app.db import get_session
from app.models import Project, SettingsLayer, SettingsScope, Task, Workspace
from app.schemas import SettingsLayerOut, WorkspaceCreate, WorkspaceOut

router = APIRouter(tags=["settings"])

SCOPE_MODELS = {SettingsScope.WORKSPACE: Workspace, SettingsScope.PROJECT: Project, SettingsScope.TASK: Task}


@router.post("/workspaces", response_model=WorkspaceOut, status_code=201)
def create_workspace(body: WorkspaceCreate, session: Session = Depends(get_session)):
    workspace = Workspace(name=body.name)
    session.add(workspace)
    session.commit()
    return workspace


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


@router.put("/settings/{scope}/{scope_id}", response_model=SettingsLayerOut)
def put_settings_layer(
    scope: SettingsScope,
    scope_id: int,
    values: dict[str, Any] = Body(...),
    session: Session = Depends(get_session),
):
    """Replace this layer's overrides. Send only the keys this scope should change."""
    _check_scope(session, scope, scope_id)
    if unknown := set(values) - set(settings_layers.DEFAULTS):
        raise HTTPException(422, f"Unknown settings: {sorted(unknown)}")
    layer = settings_layers.get_layer(session, scope, scope_id) or SettingsLayer(scope=scope, scope_id=scope_id)
    layer.values = values
    session.add(layer)
    session.commit()
    return SettingsLayerOut(scope=scope, scope_id=scope_id, values=layer.values)
