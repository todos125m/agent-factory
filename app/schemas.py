from datetime import datetime
from typing import Annotated, Any, ClassVar, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictInt, ValidationInfo, field_validator
from pydantic_core import PydanticCustomError

from app.gateway.service import WEB_SEARCH_HARD_CEILING
from app.models import (
    Feedback, MemoryCategory, MemoryItem, ModelCall, Mode, Project, ProjectStage, RiskLevel, SettingsScope, Task,
    TaskStatus, User, Workspace,
)
from app.registry import MODEL_ROLES
from app.settings_layers import MODEL_ACCESS_PROVIDERS
from app.storable import INT32_MAX, Int32, StorableIn, limit

# A USD limit for the budget gate, which blocks a call once `spent + in flight + estimate > limit`
# (app/gateway/service.py::_hold_budget). NaN and Infinity (Python's JSON parser accepts both) and
# absurd finite values such as 1e300 make that comparison never true, silently switching the gate off.
MAX_BUDGET_USD = 10_000
Usd = Annotated[float, Field(strict=True, ge=0, le=MAX_BUDGET_USD, allow_inf_nan=False)]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# Request bodies are StorableIn (app/storable.py): what can't be stored (NUL, a lone surrogate, NaN inside a JSON
# object) is a 422 naming the field. A String(n) field declares its column's own length: PostgreSQL refuses a longer
# value (SQLite doesn't), so the store would fail with a 500 on every retry; here it is a 422 before anything is written.
class UserCreate(StorableIn):
    email: str = Field(max_length=limit(User, "email"))
    name: str | None = Field(default=None, max_length=limit(User, "name"))


class UserOut(ORM):
    id: int
    email: str
    name: str | None
    created_at: datetime


class ProjectCreate(StorableIn):
    owner_id: int
    workspace_id: int | None = None
    title: str = Field(max_length=limit(Project, "title"))
    goal: str
    mode: Mode = Mode.AUTOMATIC
    budget: Usd | None = None  # feeds the resolved budget.project_usd (app/settings_layers.py::resolve)


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


class TaskCreate(StorableIn):
    title: str = Field(max_length=limit(Task, "title"))
    owner: str | None = Field(default=None, max_length=limit(Task, "owner"))
    input: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[int] = Field(default_factory=list)
    budget: float | None = None
    priority: Int32 = 0
    risk: RiskLevel = RiskLevel.LOW
    mode: Mode | None = None
    max_retries: int = Field(default=2, ge=0, le=INT32_MAX)  # Task.max_retries is a 32-bit INTEGER on PostgreSQL


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


class TaskTransition(StorableIn):
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


class WorkspaceCreate(StorableIn):
    name: str = Field(max_length=limit(Workspace, "name"))


class WorkspaceOut(ORM):
    id: int
    name: str
    created_at: datetime


class SettingsLayerOut(BaseModel):
    scope: SettingsScope
    scope_id: int
    values: dict[str, Any]


# ---------- settings layer values (the keys of app/settings_layers.py::DEFAULTS) ----------

Depth = Literal["quick", "standard", "deep"]
ApprovalRule = Literal["auto", "manager", "user"]
# The Messages API's effort levels; null sends none (the "cheap" role's default). Whether a given model
# supports a level (Haiku 4.5 takes none, OpenAI has no "max") is only known to its provider at call time.
Effort = Literal["low", "medium", "high", "xhigh", "max"]
ModelAccess = Literal[tuple(MODEL_ACCESS_PROVIDERS)]
ModelRole = Literal[tuple(sorted(MODEL_ROLES))]


def _no_whitespace(value: str) -> str:
    if any(ch.isspace() for ch in value):
        raise PydanticCustomError("whitespace", "must not contain spaces")
    return value


# Shape only: provider names are whatever the Gateway was built with (tests register their own), so
# an unregistered one is reported by Gateway.call ("unknown provider ...") when the role is used.
ProviderName = Annotated[str, Field(strict=True, min_length=1, max_length=64), AfterValidator(_no_whitespace)]
# Every call is logged with its model id (ModelCall.model), after the call has been paid for: a longer id would be
# refused by PostgreSQL there, and the call would go unrecorded and uncounted against the budget.
ModelId = Annotated[
    str, Field(strict=True, min_length=1, max_length=limit(ModelCall, "model")), AfterValidator(_no_whitespace)
]


class _LayerPart(StorableIn):
    """A settings layer stores only the keys it overrides: every field here is optional (absent =
    inherit from the layer above) and nested dicts may be partial, but a key that is sent must hold a
    valid value, and null is not "inherit". Unknown keys are rejected, since consumers ignore them."""

    model_config = ConfigDict(extra="forbid")
    _nullable: ClassVar[frozenset[str]] = frozenset()

    @field_validator("*", mode="before")
    @classmethod
    def _null_is_not_inherit(cls, value: Any, info: ValidationInfo) -> Any:
        if value is None and info.field_name not in cls._nullable:
            raise PydanticCustomError("null_setting", "null is not a value; leave the key out to inherit")
        return value


class ApprovalsLayer(_LayerPart):
    """Risk level -> who decides a checkpoint (app/manager.py::create_checkpoint)."""

    low: ApprovalRule | None = None
    medium: ApprovalRule | None = None
    high: ApprovalRule | None = None


class BudgetLayer(_LayerPart):
    project_usd: Usd | None = None
    task_usd: Usd | None = None
    # The Messages API's own range: 128K is the largest output any current Claude model allows.
    max_output_tokens: Annotated[StrictInt, Field(ge=1, le=128_000)] | None = None
    # 0 turns web search off; a value above the gateway's hard ceiling would be silently cut down to it.
    max_web_searches: Annotated[StrictInt, Field(ge=0, le=WEB_SEARCH_HARD_CEILING)] | None = None


class ContextLayer(_LayerPart):
    # task_context() drops the sections that don't fit, so at 100 chars or fewer the prompt came out
    # empty; 200K chars (~50-70K tokens) still fits every supported model's context window.
    max_chars: Annotated[StrictInt, Field(ge=500, le=200_000)] | None = None


class ModelRouteLayer(_LayerPart):
    _nullable: ClassVar[frozenset[str]] = frozenset({"effort"})

    provider: ProviderName | None = None
    model: ModelId | None = None
    effort: Effort | None = None


class SettingsLayerIn(_LayerPart):
    """The values PUT /settings/{scope}/{scope_id} accepts; stored exactly as sent once they pass."""

    mode: Mode | None = None
    depth: Depth | None = None
    # A plan is approved as a whole (§8) and each step becomes a paid task: a sanity ceiling well
    # above the default, not a recommendation.
    max_steps: Annotated[StrictInt, Field(ge=1, le=50)] | None = None
    approvals: ApprovalsLayer | None = None
    budget: BudgetLayer | None = None
    context: ContextLayer | None = None
    model_access: ModelAccess | None = None
    models: dict[ModelRole, ModelRouteLayer] | None = None


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


class FeedbackIn(StorableIn):
    project_id: int
    task_id: int | None = None
    agent: str = Field(max_length=limit(Feedback, "agent"))
    rating: str = Field(pattern=r"^(up|down)$")  # fits Feedback.rating
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
    kind: str  # "plan" | "checkpoint" | "ready" (a READY task with no checkpoint yet)
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


class MemoryItemCreate(StorableIn):
    category: MemoryCategory
    title: str = Field(max_length=limit(MemoryItem, "title"))
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
    web_searches: int
    cost_usd: float
    duration_ms: int
    ok: bool
    error: str | None
    created_at: datetime
