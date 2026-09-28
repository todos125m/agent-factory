from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models import MemoryCategory, Mode, ProjectStage, RiskLevel, SettingsScope, TaskStatus


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
    workspace_id: int | None = None
    title: str
    goal: str
    mode: Mode = Mode.AUTOMATIC
    budget: float | None = None


class ProjectOut(ORM):
    id: int
    owner_id: int
    workspace_id: int | None
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


class TaskRunResultOut(BaseModel):
    task: TaskOut
    ready_task_ids: list[int]


class RunEventOut(ORM):
    id: int
    project_id: int
    task_id: int | None
    type: str
    payload: dict[str, Any]
    created_at: datetime


class WorkspaceCreate(BaseModel):
    name: str


class WorkspaceOut(ORM):
    id: int
    name: str
    created_at: datetime


class SettingsLayerOut(BaseModel):
    scope: SettingsScope
    scope_id: int
    values: dict[str, Any]


class SkillOut(ORM):
    name: str
    version: int
    summary: str
    body: str


class SkillSummary(ORM):
    name: str
    version: int
    summary: str


class AgentOut(ORM):
    name: str
    version: int
    description: str
    model_role: str
    capabilities: list[str]
    tools: list[str]
    permissions: dict[str, Any]
    skills: list[str]
    active: bool


class ChatMessageIn(BaseModel):
    text: str


class ChatMessageOut(ORM):
    id: int
    project_id: int
    role: str
    text: str
    suggested_action: str | None
    created_at: datetime


class ChatTurnOut(BaseModel):
    user: ChatMessageOut
    manager: ChatMessageOut


class FeedbackIn(BaseModel):
    project_id: int
    task_id: int | None = None
    agent: str
    rating: str = Field(pattern=r"^(up|down)$")
    note: str | None = None


class FeedbackOut(ORM):
    id: int
    project_id: int
    task_id: int | None
    agent: str
    rating: str
    note: str | None
    created_at: datetime


class InboxItem(BaseModel):
    kind: str  # "plan" | "checkpoint"
    project_id: int
    project_title: str
    task_id: int | None = None
    challenge: str
    options: list[dict[str, Any]]
    recommended: int | None
    why: str
    created_at: datetime


class UsageOut(BaseModel):
    calls: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cost_usd: float
    budget_usd: float


class MemoryItemCreate(BaseModel):
    category: MemoryCategory
    title: str = Field(max_length=300)  # matches MemoryItem.title's String(300) column
    content: str
    task_id: int | None = None


class MemoryItemOut(ORM):
    id: int
    project_id: int
    task_id: int | None
    category: MemoryCategory
    title: str
    content: str
    created_at: datetime


class LearningTraceOut(ORM):
    id: int
    project_id: int
    task_id: int | None
    concept: str
    explanation: str
    example: str | None
    related_decision: str | None
    user_question: str | None
    mastery_signal: str | None
    created_at: datetime


class BenchmarkRunOut(ORM):
    id: int
    goal: str
    provider: str
    model: str
    effort: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    duration_ms: int
    schema_valid: bool
    error: str | None
    rating: int | None
    created_at: datetime


class ModelCallOut(ORM):
    id: int
    project_id: int | None
    task_id: int | None
    agent: str | None
    role: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float
    duration_ms: int
    ok: bool
    error: str | None
    created_at: datetime
