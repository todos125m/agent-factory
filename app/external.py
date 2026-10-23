"""The external API (PR7 of issue #2; decisions d42, d43, d44): an outside program runs ONE agent version of the owner's,
through a key the owner made, inside limits the owner set — and can do nothing the owner's approval didn't cover.

- Making a key is the owner's standing approval (d42) of exactly: an agent version the owner built, evaluated and
  activated here (d44), one of the owner's projects, the scopes, and the limits (d43: runs a day, dollars per run).
  Every call runs inside that, in the bounded runtime (app/runtime.py). Read-only tools may run; a tool action or an
  unknown outcome waits for the OWNER in the inbox. The caller can only answer the agent's questions: the API has no
  way to approve a tool, a plan, a checkpoint, a design or an activation.
- The secret is shown once, when made. Only its SHA-256 and the first characters are kept. A wrong, unknown or revoked
  key is the same answer (401); each call re-checks that the key, its agent version and its project are still valid.
- Refusals come before any spend: quota (429), open runs (429), a paused project (409), an agent that changed (409).
- A call is idempotent by `request_id`: the same id again returns the same run, never a second one.
"""

import hashlib
import secrets
from datetime import timedelta
from typing import Any

from fastapi import Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import auth, runtime
from app.db import get_session
from app.errors import AppError, not_found
from app.events import record_event
from app.manager import ensure_active
from app.models import (
    Agent, AgentVersion, ApiKey, FactorySession, Project, RiskLevel, Run, SettingsLayer, SettingsScope, Task,
    TaskStatus, User, utcnow,
)
from app.pause import check_task_move

SCOPES = ("run", "read", "answer")
TOKEN_PREFIX = "afk_"
MAX_OPEN_RUNS = 3
WINDOW = timedelta(hours=24)
DEFAULT_RUNS_PER_DAY = 20
DEFAULT_BUDGET_PER_RUN = 0.5
MAX_RUNS_PER_DAY = 10_000
MAX_BUDGET_PER_RUN = 100.0
MAX_INPUT_CHARS = 4000
API_STATES = {"queued", "running", "waiting", "completed", "stopped"}


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def invalid_key() -> AppError:
    """One answer for a missing, unknown, malformed or revoked key."""
    return AppError(401, "invalid_api_key", "A valid API key is required (Authorization: Bearer <key>)")


# ---------- the owner's side: making and revoking keys ----------


def active_agents(db: Session, owner: User) -> list[dict[str, Any]]:
    """The agent versions this owner may pin: built here, evaluated, activated, and active now (d44)."""
    rows = db.execute(
        select(AgentVersion, Agent).join(Agent, Agent.name == AgentVersion.agent_name)
        .join(FactorySession, FactorySession.id == AgentVersion.session_id)
        .where(FactorySession.owner_id == owner.id, AgentVersion.state == "active", Agent.active.is_(True),
               Agent.version == AgentVersion.version).order_by(AgentVersion.agent_name))
    return [{"name": v.agent_name, "version": v.version, "description": v.spec.get("description", "")}
            for v, _ in rows]


def _check_agent(db: Session, owner: User, name: str, version: int) -> None:
    if not any(a["name"] == name and a["version"] == version for a in active_agents(db, owner)):
        raise AppError(409, "agent_unavailable", f"'{name}' version {version} isn't an active agent you built",
                       agent=name, version=version)


def view(key: ApiKey, used_today: int) -> dict[str, Any]:
    """Everything the UI may see: never the secret."""
    return {"id": key.id, "name": key.name, "prefix": key.prefix, "agent": key.agent_name,
            "agent_version": key.agent_version, "project_id": key.project_id, "scopes": list(key.scopes),
            "max_runs_per_day": key.max_runs_per_day, "budget_per_run_usd": key.budget_per_run_usd,
            "runs_today": used_today, "revoked": key.revoked_at is not None, "last_used_at": key.last_used_at,
            "created_at": key.created_at}


def _used(db: Session, key: ApiKey) -> int:
    return db.scalar(select(func.count(Run.id)).where(Run.api_key_id == key.id,
                                                      Run.created_at > utcnow() - WINDOW)) or 0


def listing(db: Session, owner: User) -> dict[str, Any]:
    keys = list(db.scalars(select(ApiKey).where(ApiKey.owner_id == owner.id, ApiKey.revoked_at.is_(None)).order_by(ApiKey.id)))
    used = dict(db.execute(select(Run.api_key_id, func.count(Run.id)).where(
        Run.api_key_id.in_([k.id for k in keys]), Run.created_at > utcnow() - WINDOW).group_by(Run.api_key_id)).all())
    return {"items": [view(k, used.get(k.id, 0)) for k in keys], "agents": active_agents(db, owner),
            "scopes": list(SCOPES), "defaults": {"max_runs_per_day": DEFAULT_RUNS_PER_DAY,
                                                 "budget_per_run_usd": DEFAULT_BUDGET_PER_RUN}}


def create(db: Session, owner: User, *, name: str, agent: str, agent_version: int, project_id: int, scopes: list[str],
           max_runs_per_day: int, budget_per_run_usd: float, request_id: str) -> tuple[ApiKey, str | None]:
    """The key and its secret (shown once). A replayed request returns the key and no secret."""
    existing = db.scalar(select(ApiKey).where(ApiKey.owner_id == owner.id, ApiKey.request_id == request_id))
    if existing is not None:
        return existing, None
    if not scopes or set(scopes) - set(SCOPES):
        raise AppError(422, "api_key_invalid", "Choose scopes from run, read, answer", field="scopes")
    if not (1 <= max_runs_per_day <= MAX_RUNS_PER_DAY) or not (0 < budget_per_run_usd <= MAX_BUDGET_PER_RUN):
        raise AppError(422, "api_key_invalid", "Limits out of range", field="limits")
    auth.own_project(db, project_id, owner)
    _check_agent(db, owner, agent, agent_version)
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    key = ApiKey(owner_id=owner.id, name=name, prefix=token[:8], token_hash=_digest(token), agent_name=agent,
                 agent_version=agent_version, project_id=project_id, scopes=sorted(set(scopes)),
                 max_runs_per_day=max_runs_per_day, budget_per_run_usd=budget_per_run_usd, request_id=request_id)
    db.add(key)
    record_event(db, project_id, "api_key.created", None, key_prefix=key.prefix, agent=agent, agent_version=agent_version,
                 scopes=key.scopes, max_runs_per_day=max_runs_per_day, budget_per_run_usd=budget_per_run_usd)
    db.commit()
    return key, token


def revoke(db: Session, owner: User, key_id: int) -> ApiKey:
    key = db.get(ApiKey, key_id)
    if key is None or key.owner_id != owner.id:
        raise not_found("api_key")
    if key.revoked_at is None:
        key.revoked_at = utcnow()
        record_event(db, key.project_id, "api_key.revoked", None, key_prefix=key.prefix)
        db.commit()
        # What it started and is still open stops with it: no spend continues for a key that no longer exists.
        for run in db.scalars(select(Run).where(Run.api_key_id == key.id, Run.state.in_(("queued", "running", "waiting")))).all():
            runtime.cancel(db, db.get(Project, run.project_id), db.get(Task, run.task_id), run, owner)
    return key


# ---------- the caller's side ----------


def current_key(request: Request, db: Session = Depends(get_session)) -> ApiKey:
    """The key behind `Authorization: Bearer <key>`, or the one 401."""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.startswith(TOKEN_PREFIX) or len(token) > 200:
        raise invalid_key()
    key = db.scalar(select(ApiKey).where(ApiKey.token_hash == _digest(token.strip())))
    if key is None or key.revoked_at is not None:
        raise invalid_key()
    if key.last_used_at is None or utcnow() - runtime._aware(key.last_used_at) > timedelta(minutes=5):
        key.last_used_at = utcnow()
        db.commit()
    return key


def need(key: ApiKey, scope: str) -> None:
    if scope not in key.scopes:
        raise AppError(403, "api_scope_missing", f"This key doesn't have the '{scope}' scope", scope=scope)


def _owner(db: Session, key: ApiKey) -> User:
    owner = db.get(User, key.owner_id)
    if owner is None:
        raise invalid_key()
    return owner


def load_run(db: Session, key: ApiKey, run_id: int) -> Run:
    """The key's own run; another key's, another account's or a missing one is the same 404."""
    run = db.get(Run, run_id)
    if run is None or run.api_key_id != key.id:
        raise not_found("run")
    return run


def start_run(db: Session, key: ApiKey, *, text: str, request_id: str) -> tuple[Run, bool]:
    need(key, "run")
    locked = db.scalar(select(ApiKey).where(ApiKey.id == key.id).with_for_update().execution_options(populate_existing=True))
    if locked is None or locked.revoked_at is not None:
        raise invalid_key()
    key = locked
    existing = db.scalar(select(Run).where(Run.api_key_id == key.id, Run.api_request_id == request_id))
    if existing is not None:
        if (db.get(Task, existing.task_id).input or {}).get("goal") != text:
            raise AppError(409, "api_request_reused", "That request_id was used for a different input")
        return existing, False
    owner = _owner(db, key)
    project = db.get(Project, key.project_id)
    if project is None or project.owner_id != owner.id:
        raise AppError(409, "agent_unavailable", "The key's project isn't available", agent=key.agent_name,
                       version=key.agent_version)
    _check_agent(db, owner, key.agent_name, key.agent_version)  # still the active version the owner approved
    ensure_active(db, project, "api.run")
    if _used(db, key) >= key.max_runs_per_day:
        raise AppError(429, "quota_exceeded", "This key's runs for the last 24 hours are used up",
                       limit=key.max_runs_per_day)
    open_runs = db.scalar(select(func.count(Run.id)).where(Run.api_key_id == key.id,
                                                            Run.state.in_(("queued", "running", "waiting")))) or 0
    if open_runs >= MAX_OPEN_RUNS:
        raise AppError(429, "too_many_open_runs", f"At most {MAX_OPEN_RUNS} runs of one key at a time",
                       limit=MAX_OPEN_RUNS)

    task = Task(project_id=project.id, title=("API: " + " ".join(text.split()))[:120], owner=key.agent_name,
                input={"goal": text, "api_key": key.id}, risk=RiskLevel.LOW)
    db.add(task)
    db.flush()
    for target in (TaskStatus.READY, TaskStatus.RUNNING):  # the key's standing approval stands for the checkpoint
        check_task_move(db, task, target, dependency_statuses=[])
        was, task.status = task.status.value, target
        record_event(db, project.id, "task.status_changed", task.id,
                     **{"from": was, "to": target.value, "reason": f"api key {key.prefix}"})
    db.add(SettingsLayer(scope=SettingsScope.TASK, scope_id=task.id,
                         values={"budget": {"task_usd": key.budget_per_run_usd}}))
    db.flush()
    record_event(db, project.id, "api.run_started", task.id, key_prefix=key.prefix, request_id=request_id)
    run = runtime.start(db, project, task, owner, api_key_id=key.id, api_request_id=request_id)
    return run, True


def run_view(db: Session, run: Run) -> dict[str, Any]:
    """What the caller may see of a run: its state, what it waits for, its result. Never tool inputs, steps or owner-only
    questions."""
    pending = run.pending or {}
    waiting_for = None
    if run.state == "waiting":
        waiting_for = "answer" if pending.get("kind") == "question" else "owner"
    task = db.get(Task, run.task_id)
    return {"run_id": run.id, "state": run.state, "reason": run.reason,
            "waiting_for": waiting_for, "question": pending.get("text") if waiting_for == "answer" else None,
            "result": task.output if run.state == "completed" and task is not None else None,
            "steps_used": run.steps_used, "created_at": run.created_at, "finished_at": run.finished_at}


def answer_run(db: Session, key: ApiKey, run_id: int, text: str) -> Run:
    need(key, "answer")
    run = load_run(db, key, run_id)
    if run.state != "waiting" or (run.pending or {}).get("kind") != "question":
        raise AppError(409, "not_waiting_for_answer", "This run isn't waiting for an answer from you")
    owner, project, task = _owner(db, key), db.get(Project, run.project_id), db.get(Task, run.task_id)
    record_event(db, project.id, "api.run_answered", task.id, key_prefix=key.prefix, run_id=run.id)
    return runtime.respond(db, project, task, run, owner, text=text, approve=None, happened=None,
                           via=f"api_key:{key.id}")


def cancel_run(db: Session, key: ApiKey, run_id: int) -> Run:
    need(key, "run")
    run = load_run(db, key, run_id)
    owner, project, task = _owner(db, key), db.get(Project, run.project_id), db.get(Task, run.task_id)
    return runtime.cancel(db, project, task, run, owner)
