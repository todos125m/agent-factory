"""Data model (docs/ARCHITECTURE.md §10, §22, §23, §31, §40, §50).

Foundation: User, Workspace, Project, Task, TaskDependency, RunEvent.
Infrastructure: SettingsLayer, Agent, Skill, ModelCall, BudgetHold.
Approvals, learning traces and memory arrive in later phases.
"""

import enum
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# A task's title is copied whole into MemoryItem.title and LearningTrace.concept when its run completes (app/memory.py),
# after the paid call: a copy in a narrower column is refused by PostgreSQL (SQLite takes it), so all three are this long.
TASK_TITLE_LENGTH = 300


class Mode(str, enum.Enum):
    AUTOMATIC = "automatic"
    MANUAL_LEARNING = "manual_learning"


class RiskLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ProjectStage(str, enum.Enum):
    IDEA = "IDEA"
    DISCOVERY = "DISCOVERY"
    VALIDATION = "VALIDATION"
    PRODUCT_DEFINITION = "PRODUCT_DEFINITION"
    BUILDING = "BUILDING"
    REVIEW = "REVIEW"
    LAUNCH = "LAUNCH"
    MEASUREMENT = "MEASUREMENT"
    ITERATION = "ITERATION"


class TaskStatus(str, enum.Enum):
    CREATED = "CREATED"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    REVIEWED = "REVIEWED"
    FAILED = "FAILED"
    ESCALATED = "ESCALATED"
    CANCELLED = "CANCELLED"
    # Kept in the graph, with every dependency, but unable to start: its specialist doesn't exist (`Task.reason`).
    BLOCKED = "BLOCKED"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # The server's owner: the only one who may change what every user shares (global settings, the agent registry,
    # benchmarks). The first account of a server gets it (app/auth.py::create_account; migration 0002 for old ones).
    is_admin: Mapped[bool] = mapped_column(default=False, server_default=false())
    # An account from before login existed (migration 0002): it has no session yet, and may be claimed once by the
    # browser that made it (app/auth.py::claim). A new account is signed in when it is made, so never claimable.
    claimable: Mapped[bool] = mapped_column(default=False, server_default=false())


class AuthSession(Base):
    """A signed-in browser (docs/HUMAN_GUIDED_FACTORY.md, owner decision d33). Only the token's SHA-256 is stored: the
    token itself lives in the browser's HttpOnly cookie, so a leaked database signs no one in."""

    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LoginCode(Base):
    """A one-time code a signed-in owner makes to sign in another device (no passwords, no e-mail). Short-lived and
    single-use; only its SHA-256 is stored."""

    __tablename__ = "login_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserPreference(Base):
    """How the UI talks to its owner (docs/HUMAN_GUIDED_FACTORY.md §4): language, depth of explanation, whether code
    is shown. Display only: no permission, approval, budget or policy reads these (app/preferences.py). A None is
    "not chosen yet" — the UI asks for the language on first entry and proposes a level the owner confirms."""

    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    ui_locale: Mapped[str | None] = mapped_column(String(8))
    audience_level: Mapped[str | None] = mapped_column(String(20))
    code_visibility: Mapped[str | None] = mapped_column(String(10))  # None = follow audience_level
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Whose it is (migration 0002 gives the workspaces made before login to the admin). Never None for a new one.
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    workspace_id: Mapped[int | None] = mapped_column(ForeignKey("workspaces.id"))
    title: Mapped[str] = mapped_column(String(300))
    goal: Mapped[str] = mapped_column(Text)
    mode: Mapped[Mode] = mapped_column(Enum(Mode), default=Mode.AUTOMATIC)
    stage: Mapped[ProjectStage] = mapped_column(Enum(ProjectStage), default=ProjectStage.IDEA)
    paused: Mapped[bool] = mapped_column(default=False)
    budget: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    tasks: Mapped[list["Task"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(TASK_TITLE_LENGTH))
    owner: Mapped[str | None] = mapped_column(String(100))  # agent name from the registry (Phase 3)
    input: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[TaskStatus] = mapped_column(Enum(TaskStatus), default=TaskStatus.CREATED)
    budget: Mapped[float | None] = mapped_column(Float)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    risk: Mapped[RiskLevel] = mapped_column(Enum(RiskLevel), default=RiskLevel.LOW)
    mode: Mapped[Mode | None] = mapped_column(Enum(Mode))  # None = inherit from project
    retries: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=2)
    # Why it is BLOCKED or WAITING (or why its run stopped): a code, not prose — missing_specialist, waiting_user,
    # tool_approval, reconcile, step_limit, time_limit, budget_exhausted, model_failed, schema_mismatch, run_cancelled.
    reason: Mapped[str | None] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    project: Mapped[Project] = relationship(back_populates="tasks")
    dependencies: Mapped[list["TaskDependency"]] = relationship(
        foreign_keys="TaskDependency.task_id", cascade="all, delete-orphan"
    )

    @property
    def depends_on(self) -> list[int]:
        return [d.depends_on_id for d in self.dependencies]


class TaskDependency(Base):
    __tablename__ = "task_dependencies"
    __table_args__ = (UniqueConstraint("task_id", "depends_on_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    depends_on_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))


class RunEvent(Base):
    """Append-only audit log: every state transition is recorded here (§23, §49) — a project's, or an agent-building
    session's (`factory_session_id`, app/factory.py): exactly one of the two is set."""

    __tablename__ = "run_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    factory_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("factory_sessions.id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SettingsScope(str, enum.Enum):
    GLOBAL = "global"
    WORKSPACE = "workspace"
    PROJECT = "project"
    TASK = "task"


class SettingsLayer(Base):
    """One layer of settings; lower scopes override higher ones (see app/settings_layers.py)."""

    __tablename__ = "settings_layers"
    __table_args__ = (UniqueConstraint("scope", "scope_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[SettingsScope] = mapped_column(Enum(SettingsScope))
    scope_id: Mapped[int] = mapped_column(Integer, default=0)  # 0 for the global layer
    values: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Skill(Base):
    """A unit of know-how an agent can load on demand. Only `summary` sits in the prompt by default."""

    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    summary: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)


class Agent(Base):
    """Agent Registry entry (docs/ARCHITECTURE.md §10)."""

    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    description: Mapped[str] = mapped_column(Text)
    model_role: Mapped[str] = mapped_column(String(50))
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list)
    tools: Mapped[list[str]] = mapped_column(JSON, default=list)
    permissions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    instructions: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(default=True)


class ChatMessage(Base):
    """One turn in the owner ↔ manager chat for a project (§ manager checkpoint UX)."""

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20))  # "user" | "manager"
    text: Mapped[str] = mapped_column(Text)
    suggested_action: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Feedback(Base):
    """Owner feedback on an agent's output (§8 Learning UX). Turning this into skill
    updates is a later phase; for now it is only stored and shown."""

    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    agent: Mapped[str] = mapped_column(String(100))
    rating: Mapped[str] = mapped_column(String(10))  # "up" | "down"
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MemoryCategory(str, enum.Enum):
    BRIEF = "brief"
    DECISIONS = "decisions"
    RESEARCH = "research"
    CUSTOMER = "customer"
    STRATEGY = "strategy"
    PRODUCT = "product"
    TECHNICAL = "technical"
    EXPERIMENTS = "experiments"
    LEARNING = "learning"


class MemoryItem(Base):
    """Long-term Project Memory (docs/ARCHITECTURE.md §24): selective, category-tagged notes an
    agent's context can retrieve from — never the whole project history (app/context.py)."""

    __tablename__ = "memory_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    category: Mapped[MemoryCategory] = mapped_column(Enum(MemoryCategory))
    title: Mapped[str] = mapped_column(String(TASK_TITLE_LENGTH))  # a completed task's title, or the owner's own note's
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LearningTrace(Base):
    """§26 Learning Memory: a lesson captured in manual_learning mode, kept separate from the task's
    own output — Manual Mode must not only store the result, but also what it taught the owner."""

    __tablename__ = "learning_traces"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    concept: Mapped[str] = mapped_column(String(TASK_TITLE_LENGTH))  # the completed task's title (app/memory.py)
    explanation: Mapped[str] = mapped_column(Text)
    example: Mapped[str | None] = mapped_column(Text)
    related_decision: Mapped[str | None] = mapped_column(Text)
    user_question: Mapped[str | None] = mapped_column(Text)
    mastery_signal: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BenchmarkRun(Base):
    """One route config (provider/model/effort) tried against the fixed benchmark goal (owner
    decision d12): tokens/cost/duration/schema-validity plus an optional owner 1-5 rating."""

    __tablename__ = "benchmark_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    goal: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(100))
    effort: Mapped[str | None] = mapped_column(String(20))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    schema_valid: Mapped[bool] = mapped_column(default=False)
    error: Mapped[str | None] = mapped_column(Text)
    rating: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ModelCall(Base):
    """Every model call, for token/cost tracking and budgets (§31, §34)."""

    __tablename__ = "model_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Indexed: every paid call sums its project's and task's logged cost while it holds the project's queue.
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), index=True)
    # A call made for building an agent (app/design.py), counted against that build's own budget.
    factory_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("factory_sessions.id", ondelete="SET NULL"), index=True
    )
    agent: Mapped[str | None] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(50))
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    web_searches: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(default=True)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BudgetHold(Base):
    """A paid model call's worst-case cost, held against its budgets while the call runs (§31), so a concurrent
    call's budget check counts it (app/gateway/service.py). The call's ModelCall row replaces it; one left by a
    process that stopped mid-call stops counting after HOLD_TTL there."""

    __tablename__ = "budget_holds"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    # Like ModelCall.task_id: a task deleted mid-call (a rejected plan's) must not take the hold, and with it the
    # project's in-flight spend, along; the hold keeps counting against the project until the call finishes.
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), index=True)
    factory_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("factory_sessions.id", ondelete="CASCADE"), index=True
    )
    usd: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FactoryStage(str, enum.Enum):
    """Building one agent (docs/HUMAN_GUIDED_FACTORY.md §5). Not the Task state machine: this is the build's own."""

    DRAFT = "DRAFT"
    CLARIFYING = "CLARIFYING"
    AWAITING_BRIEF_APPROVAL = "AWAITING_BRIEF_APPROVAL"
    DESIGNING = "DESIGNING"
    AWAITING_DESIGN_APPROVAL = "AWAITING_DESIGN_APPROVAL"
    BUILDING = "BUILDING"
    EVALUATING = "EVALUATING"
    AWAITING_ACTIVATION_APPROVAL = "AWAITING_ACTIVATION_APPROVAL"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class FactorySession(Base):
    """One owner building one agent with the factory (app/factory.py). `revision` grows with every change: a request
    that names an older one is refused (409), so a second tab or a late answer can't overwrite what it didn't see."""

    __tablename__ = "factory_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    stage: Mapped[str] = mapped_column(String(40), default=FactoryStage.DRAFT.value)
    stage_before_pause: Mapped[str | None] = mapped_column(String(40))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    # The language its proposals were written in (the owner's UI language when it was made); questions follow the UI.
    locale: Mapped[str] = mapped_column(String(8))
    interaction_policy: Mapped[str] = mapped_column(String(30))
    quality_target: Mapped[str] = mapped_column(String(30))
    # The most the model may cost while building this agent (assisted design, app/design.py); every paid call is
    # held against it before it runs, like a project's budget (app/gateway/service.py).
    budget_usd: Mapped[float] = mapped_column(Float, default=1.0, server_default="1.0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class BriefRevision(Base):
    """An immutable version of a session's brief: every field as {value, status, source, revision}. A change is a new
    row, never an edit, so an approval names exactly what was approved."""

    __tablename__ = "brief_revisions"
    __table_args__ = (UniqueConstraint("session_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("factory_sessions.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Approval(Base):
    """A decision by a signed-in account on one exact version of one thing (the brief, in PR2). It stops counting the
    moment that thing changes (`invalidated_at`): nothing approves a version it wasn't given."""

    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("factory_sessions.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    stage: Mapped[str] = mapped_column(String(40))
    resource: Mapped[str] = mapped_column(String(30))  # "brief" | "blueprint" | "agent_version"
    resource_version: Mapped[int] = mapped_column(Integer)
    scope: Mapped[list[str]] = mapped_column(JSON, default=list)  # the brief fields it covers
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidated_reason: Mapped[str | None] = mapped_column(String(100))


class FactoryRequest(Base):
    """A request id already applied (app/factory.py): the same request again — a double click, a retry after a lost
    answer — changes nothing a second time."""

    __tablename__ = "factory_requests"
    __table_args__ = (UniqueConstraint("owner_id", "request_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    request_id: Mapped[str] = mapped_column(String(64))
    session_id: Mapped[int | None] = mapped_column(ForeignKey("factory_sessions.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BlueprintRevision(Base):
    """An immutable version of the agent's design (docs/AGENT_BLUEPRINT.md, 14 sections), made from one approved brief
    version (app/design.py). `branches` holds each canvas branch's status — proposed / confirmed — and where a value
    came from; `issues` is the deterministic validator's verdict on exactly this version."""

    __tablename__ = "blueprint_revisions"
    __table_args__ = (UniqueConstraint("session_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("factory_sessions.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    brief_version: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    blueprint: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    branches: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    issues: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reason: Mapped[str] = mapped_column(String(40))  # drafted | assisted | edited | branch_confirmed | reverted
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Run(Base):
    """One bounded execution of a RUNNING task by its agent (app/runtime.py): the snapshot of everything it may do —
    limits, policy, permissions, tools — fixed when it starts, then its counters. A worker holds it by a lease that it
    renews each step; a lease that ran out means the worker stopped, and another one recovers the run."""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    agent: Mapped[str] = mapped_column(String(100))
    agent_version: Mapped[int] = mapped_column(Integer)
    model_role: Mapped[str] = mapped_column(String(50))
    # queued|running|waiting|completed|stopped ("state", not "status": only a Task has a status)
    state: Mapped[str] = mapped_column(String(20), default="queued")
    reason: Mapped[str | None] = mapped_column(String(60))
    limits: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    policy: Mapped[str] = mapped_column(String(30))
    permissions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    tools: Mapped[list[str]] = mapped_column(JSON, default=list)
    pending: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # what it waits on: a question, a tool approval
    steps_used: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RunStep(Base):
    """One step of a run, as it happened: the model's proposal, a tool's observation, a question and its answer."""

    __tablename__ = "run_steps"
    __table_args__ = (UniqueConstraint("run_id", "n"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    n: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))  # proposal|observation|question|answer|approval|stop|finish
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ToolExecution(Base):
    """A tool a run used (app/runtime.py Tool Gateway). Written `started` before the tool runs and `succeeded` /
    `failed` after it: a run recovered with one still `started` never runs it again — it becomes `unknown` and the
    owner says what happened (there is no general exactly-once; `idempotency_key` makes a retried step a no-op)."""

    __tablename__ = "tool_executions"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    step_n: Mapped[int] = mapped_column(Integer)
    tool: Mapped[str] = mapped_column(String(50))
    input_digest: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    state: Mapped[str] = mapped_column(String(20))  # started|succeeded|failed|unknown (a refusal runs nothing)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentVersion(Base):
    """One immutable build of a designed agent (app/build.py): the registry spec made from one approved blueprint
    version. It becomes the active Agent only after its evaluation passed and the owner approved it; versions of one
    agent name are numbered across its builds, and an earlier active one can be brought back."""

    __tablename__ = "agent_versions"
    __table_args__ = (UniqueConstraint("agent_name", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("factory_sessions.id", ondelete="CASCADE"), index=True)
    agent_name: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[int] = mapped_column(Integer)
    blueprint_version: Mapped[int] = mapped_column(Integer)
    spec: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(20), default="built")  # built|passed|failed|active|retired
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Evaluation(Base):
    """One evaluation round of a version: every test case's reply, the code check and the judge's verdict."""

    __tablename__ = "evaluations"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("factory_sessions.id", ondelete="CASCADE"), index=True)
    agent_version_id: Mapped[int] = mapped_column(ForeignKey("agent_versions.id", ondelete="CASCADE"), index=True)
    round: Mapped[int] = mapped_column(Integer)
    verdict: Mapped[str] = mapped_column(String(20))  # passed | changes_required
    cases: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Workflow(Base):
    """Agents connected on a canvas (app/workflow.py). Its meaning lives in immutable WorkflowVersions; `layout` is only
    where the nodes sit, so moving one is not a change. `revision` counts the semantic changes (optimistic concurrency)."""

    __tablename__ = "workflows"
    __table_args__ = (UniqueConstraint("owner_id", "request_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    request_id: Mapped[str | None] = mapped_column(String(64))  # the create request that made it (replay finds it)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    layout: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkflowVersion(Base):
    """One immutable version of a workflow: nodes (each pinned to an exact agent version), typed edges with their
    mappings, and the validator's issues at the time it was saved. Approved by the owner as exactly this version."""

    __tablename__ = "workflow_versions"
    __table_args__ = (UniqueConstraint("workflow_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    nodes: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    edges: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    issues: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Credential(Base):
    """A provider API key the owner added (app/credentials.py): kept encrypted with a master secret from the environment,
    shown only masked, referred to elsewhere as `cred:<id>`. Revoking wipes `ciphertext` (the row stays so a reference
    to it says "revoked")."""

    __tablename__ = "credentials"
    __table_args__ = (UniqueConstraint("owner_id", "request_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    provider: Mapped[str] = mapped_column(String(20))  # anthropic | openai
    ciphertext: Mapped[str] = mapped_column(Text, default="")
    last4: Mapped[str] = mapped_column(String(4))
    request_id: Mapped[str | None] = mapped_column(String(64))
    test_result: Mapped[str | None] = mapped_column(String(20))  # ok | rejected | unreachable
    tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
