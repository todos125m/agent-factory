"""Phase 1 data model (docs/ARCHITECTURE.md §22, §23, §40, §50).

Only the foundation entities live here: User, Project, Task, TaskDependency, RunEvent.
Agent registry, approvals, learning traces, etc. arrive in later phases.
"""

import enum
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


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


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
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
    title: Mapped[str] = mapped_column(String(300))
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
