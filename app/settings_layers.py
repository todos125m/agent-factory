"""Layered settings: defaults ← global ← workspace ← project ← task.

Each layer stores only the keys it overrides; nested dicts merge key by key,
any other value (lists included) replaces the one above it. PUT /settings validates a layer's
values against app/schemas.py::SettingsLayerIn (keep it in step with DEFAULTS) before storing them.
"""

import copy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Mode, Project, SettingsLayer, SettingsScope, Task

DEFAULTS: dict[str, Any] = {
    "mode": "manual_learning",  # automatic | manual_learning
    # interaction_policy has no default of its own: unless a layer sets it, it follows the mode (MODE_POLICY), so
    # the existing projects keep exactly the autonomy they had. resolve() always returns it.
    # The loosest interaction_policy allowed at this scope and every scope below it: a lower layer can only
    # tighten it (resolve() takes the strictest), so a project or task can't undo an owner-wide limit.
    "max_interaction_policy": "delegated_bounded",
    "quality_target": "prototype",  # prototype | production_candidate: depth of evaluation, never authority
    "output_locale": "auto",  # auto (the owner's UI language) | fa | en | tr: the language of model replies
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
        "manager": {"provider": "anthropic", "model": "claude-opus-5", "effort": "medium", "credential_ref": None},
        "research": {"provider": "anthropic", "model": "claude-sonnet-5", "effort": "low", "credential_ref": None},
        "coding": {"provider": "anthropic", "model": "claude-opus-5", "effort": "high", "credential_ref": None},
        "cheap": {"provider": "anthropic", "model": "claude-haiku-4-5", "effort": None, "credential_ref": None},
    },
}

# How much the system may do between two approvals (docs/HUMAN_GUIDED_FACTORY.md §4), strictest first. Every one is
# interactive: step_by_step asks at each step, milestone at the main points, delegated_bounded acts only inside an
# approved scope and stops on ambiguity or a change of scope. It never adds a permission, tool, budget or retry.
INTERACTION_POLICIES = ("step_by_step", "milestone", "delegated_bounded")
# A new agent is built step by step unless the owner chooses otherwise (the factory's own default).
FACTORY_DEFAULT_POLICY = "step_by_step"
# The legacy modes, mapped without granting anything they didn't allow. manual_learning waits for the owner at every
# checkpoint: step_by_step, and no layer can loosen it (MODE_CEILING) — loosening means choosing another mode, openly.
# automatic lets the manager decide the checkpoints its approval rules allow once the owner approved the plan, the
# main point: milestone by default. The mode keeps its own meaning (learning traces in manual_learning); the policy
# only ever narrows what the mode allows (app/manager.py::create_checkpoint).
MODE_POLICY = {"manual_learning": "step_by_step", "automatic": "milestone"}
MODE_CEILING = {"manual_learning": "step_by_step", "automatic": "delegated_bounded"}
QUALITY_TARGETS = ("prototype", "production_candidate")
OUTPUT_LOCALES = ("auto", "fa", "en", "tr")
# Keys a layer may set that DEFAULTS doesn't hold, because resolve() derives their default.
DERIVED_KEYS = frozenset({"interaction_policy"})


def strictest(*policies: Any) -> str:
    """The most restrictive of `policies`; anything that isn't a known policy counts as step_by_step (fail closed)."""
    rank = {p: i for i, p in enumerate(INTERACTION_POLICIES)}
    return INTERACTION_POLICIES[min(rank.get(p, 0) if isinstance(p, str) else 0 for p in policies)]


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


def _mode_value(mode: Mode | str) -> str:
    # Loaded rows hold a Mode member; an object built in Python but not yet flushed may hold the raw string.
    return mode.value if isinstance(mode, Mode) else str(mode)


def get_layer(session: Session, scope: SettingsScope, scope_id: int) -> SettingsLayer | None:
    return session.scalar(select(SettingsLayer).where(SettingsLayer.scope == scope, SettingsLayer.scope_id == scope_id))


def resolve(
    session: Session,
    *,
    workspace_id: int | None = None,
    project_id: int | None = None,
    task_id: int | None = None,
) -> dict[str, Any]:
    """Effective settings for the most specific scope given; missing ids are filled from the task/project.

    Precedence, lowest to highest: DEFAULTS ← global layer ← workspace layer ← the project's own
    fields (`Project.budget`, `Project.mode`) ← project layer ← the task's own `Task.mode` (when set)
    ← task layer. An entity's own fields are the base of its scope; an explicit settings layer at that
    same scope still overrides them. Every project has a mode (chosen at creation), so a global or
    workspace "mode" only applies where no project is in scope.

    Two keys are not a plain override. `max_interaction_policy` is the strictest any layer set, or the mode allows
    (MODE_CEILING): a lower layer can tighten it, never loosen it. `interaction_policy` is the lowest layer's, or the
    mode's (MODE_POLICY) when no layer set one, then clamped to that ceiling: the result is what the system may
    actually do.
    """
    task = session.get(Task, task_id) if task_id is not None else None
    if task is not None and project_id is None:
        project_id = task.project_id
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
    ceilings = [DEFAULTS["max_interaction_policy"]]
    for scope, scope_id in chain:
        if scope_id is None:
            continue
        if scope is SettingsScope.PROJECT and project is not None:
            if project.budget is not None:
                result = deep_merge(result, {"budget": {"project_usd": project.budget}})
            if project.mode is not None:
                result["mode"] = _mode_value(project.mode)
        if scope is SettingsScope.TASK and task is not None and task.mode is not None:
            result["mode"] = _mode_value(task.mode)
        layer = get_layer(session, scope, scope_id)
        if layer is not None:
            models_override = layer.values.get("models")
            if isinstance(models_override, dict):
                for role, cfg in models_override.items():
                    if isinstance(cfg, dict) and "provider" in cfg:
                        explicit_role_provider.add(role)
                    if isinstance(cfg, dict) and "model" in cfg:
                        explicit_role_model.add(role)
            if "max_interaction_policy" in layer.values:
                ceilings.append(layer.values["max_interaction_policy"])
            result = deep_merge(result, layer.values)

    mode = result.get("mode")
    mode = mode if isinstance(mode, str) else None  # a stored non-string (written around PUT's validation) fails closed
    result["max_interaction_policy"] = strictest(*ceilings, MODE_CEILING.get(mode, "step_by_step"))
    requested = result.get("interaction_policy") or MODE_POLICY.get(mode, "step_by_step")
    result["interaction_policy"] = strictest(requested, result["max_interaction_policy"])

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
