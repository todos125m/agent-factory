"""Data model (docs/ARCHITECTURE.md §10, §22, §23, §31, §40, §50).

Foundation: User, Workspace, Project, Task, TaskDependency, RunEvent.
Infrastructure: SettingsLayer, Agent, Skill, ModelCall, BudgetHold.
Approvals, learning traces and memory arrive in later phases.
"""

import enum
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
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


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(primary_key=True)
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
    """Append-only audit log: every state transition is recorded here (§23, §49)."""

    __tablename__ = "run_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
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
    usd: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
