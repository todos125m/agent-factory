"""Layered settings: defaults ← global ← workspace ← project ← task.

Each layer stores only the keys it overrides; nested dicts merge key by key,
any other value (lists included) replaces the one above it.
"""

import copy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Project, SettingsLayer, SettingsScope, Task

DEFAULTS: dict[str, Any] = {
    "mode": "manual_learning",  # automatic | manual_learning
    "depth": "standard",  # quick | standard | deep
    "max_steps": 6,
    "approvals": {"low": "auto", "medium": "manager", "high": "user"},
    "budget": {
        "project_usd": 5.0,
        "task_usd": 1.0,
        "max_output_tokens": 2000,
        "max_web_searches": 5,  # per call; Gateway.call() also enforces a hard ceiling (see WEB_SEARCH_HARD_CEILING)
    },
    "context": {"max_chars": 6000},
    # Owner-wide convenience switch (§ model access modes): "api_key" or "claude_account". Applied to
    # every role that doesn't set its own "provider" explicitly in some settings layer (see resolve()).
    "model_access": "claude_account",
    # Agents name a role, never a model ID (§29). Each role maps to a provider + model here.
    "models": {
        "manager": {"provider": "anthropic", "model": "claude-opus-5", "effort": "medium"},
        "research": {"provider": "anthropic", "model": "claude-sonnet-5", "effort": "low"},
        "coding": {"provider": "anthropic", "model": "claude-opus-5", "effort": "high"},
        "cheap": {"provider": "anthropic", "model": "claude-haiku-4-5", "effort": None},
    },
}

MODEL_ACCESS_PROVIDERS: dict[str, str] = {
    "api_key": "anthropic",
    "claude_account": "claude_account",
    "ollama": "ollama",
}

# api_key/claude_account keep using Claude model IDs, so only a mode whose provider has a different
# model namespace (Ollama's local catalog) needs its roles' "model" rewritten too — only when a
# layer didn't already set one explicitly (see resolve()'s explicit_role_model).
MODEL_ACCESS_DEFAULT_MODEL: dict[str, str] = {"ollama": "llama3.2"}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def get_layer(session: Session, scope: SettingsScope, scope_id: int) -> SettingsLayer | None:
    return session.scalar(select(SettingsLayer).where(SettingsLayer.scope == scope, SettingsLayer.scope_id == scope_id))


def resolve(
    session: Session,
    *,
    workspace_id: int | None = None,
    project_id: int | None = None,
    task_id: int | None = None,
) -> dict[str, Any]:
    """Effective settings for the most specific scope given; missing ids are filled from the task/project."""
    if task_id is not None and project_id is None:
        task = session.get(Task, task_id)
        project_id = task.project_id if task else None
    project = session.get(Project, project_id) if project_id is not None else None
    if project is not None and workspace_id is None:
        workspace_id = project.workspace_id

    chain: list[tuple[SettingsScope, int | None]] = [
        (SettingsScope.GLOBAL, 0),
        (SettingsScope.WORKSPACE, workspace_id),
        (SettingsScope.PROJECT, project_id),
        (SettingsScope.TASK, task_id),
    ]
    result = copy.deepcopy(DEFAULTS)
    explicit_role_provider: set[str] = set()
    explicit_role_model: set[str] = set()
    for scope, scope_id in chain:
        if scope_id is None:
            continue
        if scope is SettingsScope.PROJECT and project is not None and project.budget is not None:
            result = deep_merge(result, {"budget": {"project_usd": project.budget}})
        layer = get_layer(session, scope, scope_id)
        if layer is not None:
            models_override = layer.values.get("models")
            if isinstance(models_override, dict):
                for role, cfg in models_override.items():
                    if isinstance(cfg, dict) and "provider" in cfg:
                        explicit_role_provider.add(role)
                    if isinstance(cfg, dict) and "model" in cfg:
                        explicit_role_model.add(role)
            result = deep_merge(result, layer.values)

    provider = MODEL_ACCESS_PROVIDERS.get(result.get("model_access"))
    default_model = MODEL_ACCESS_DEFAULT_MODEL.get(result.get("model_access"))
    if provider is not None and isinstance(result.get("models"), dict):
        for role, cfg in result["models"].items():
            if not isinstance(cfg, dict):
                continue
            if role not in explicit_role_provider:
                cfg["provider"] = provider
            if default_model is not None and role not in explicit_role_model:
                cfg["model"] = default_model
    return result
