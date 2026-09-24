from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models import Mode, ProjectStage, RiskLevel, TaskStatus


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserCreate(BaseModel):
    email: str
    name: str | None = None


class UserOut(ORM):
    id: int
    email: str
    name: str | None
    created_at: datetime


class ProjectCreate(BaseModel):
    owner_id: int
    title: str
    goal: str
    mode: Mode = Mode.AUTOMATIC
    budget: float | None = None


class ProjectOut(ORM):
    id: int
    owner_id: int
    title: str
    goal: str
    mode: Mode
    stage: ProjectStage
    paused: bool
    budget: float | None
    created_at: datetime


class ProjectStageUpdate(BaseModel):
    stage: ProjectStage


class ProjectPauseUpdate(BaseModel):
    paused: bool


class TaskCreate(BaseModel):
    title: str
    owner: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[int] = Field(default_factory=list)
    budget: float | None = None
    priority: int = 0
    risk: RiskLevel = RiskLevel.LOW
    mode: Mode | None = None
    max_retries: int = Field(default=2, ge=0)


class TaskOut(ORM):
    id: int
    project_id: int
    title: str
    owner: str | None
    input: dict[str, Any]
    output: dict[str, Any] | None
    status: TaskStatus
    budget: float | None
    priority: int
    risk: RiskLevel
    mode: Mode | None
    retries: int
    max_retries: int
    depends_on: list[int]
    created_at: datetime
    updated_at: datetime


class TaskTransition(BaseModel):
    status: TaskStatus
    output: dict[str, Any] | None = None
    reason: str | None = None


class RunEventOut(ORM):
    id: int
    project_id: int
    task_id: int | None
    type: str
    payload: dict[str, Any]
    created_at: datetime
