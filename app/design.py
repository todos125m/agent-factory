"""Designing the agent from its approved brief (PR3 of issue #2; docs/HUMAN_GUIDED_FACTORY.md §12, FACTORY_UI_SPEC §3.2).

- The first draft is deterministic: approving the brief draws a 14-section Blueprint (docs/AGENT_BLUEPRINT.md) from it.
  What an ordinary owner can't be asked (model role, storage, deployment, the observation record) is filled by the
  system as the standard says, and shown with its reason, never asked.
- The canvas groups the sections into eight branches (BRANCHES). Each branch is proposed until the owner confirms it;
  an edit makes a new immutable BlueprintRevision and puts the edited branch back to proposed. Undo restores an
  earlier version as a new one (history is never rewritten).
- Validation is code (app/blueprint.py plus the design rules here): an issue keeps its branch "incomplete" and blocks
  the design approval. Permissions are off unless the owner turns them on in the interaction branch, a tool needs its
  permission, and the approval gate can't be looser than the build's interaction policy.
- "Improve with AI" is the only model call, made only when the owner asks: role "cheap", JSON-schema reply, held
  against the build's own budget (FactorySession.budget_usd, app/gateway/service.py) before it runs. Its suggestions
  land as proposals, never as confirmed.
- Approving the design binds to one exact blueprint version, made from the brief version that is approved now, and
  moves the build to BUILDING (PR5). Changing the brief afterwards invalidates both approvals.
"""

import copy
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import context, factory, storable
from app.blueprint import validate_blueprint
from app.errors import AppError
from app.events import record_factory_event
from app.gateway.service import Gateway, build_held_usd, build_spent_usd
from app.models import Approval, BlueprintRevision, FactorySession, FactoryStage, User, utcnow
from app.registry import KNOWN_TOOLS, MODEL_ROLES

S = FactoryStage
SCHEMA_VERSION = 1

# Canvas branch -> the Blueprint sections it shows (FACTORY_UI_SPEC §3.2: every one of the 14 is covered).
BRANCHES: dict[str, list[str]] = {
    "identity": ["agent_core", "persona"],  # 1 Core, 2 Persona (absent for a specialist)
    "io_actions": ["action_engine", "io_contract"],  # 4 Action + the versioned I/O annex
    "knowledge": ["knowledge_boundary"],  # 3
    "tools": ["tools_plugins", "external_integrations"],  # 5, 10
    "memory": ["memory", "observation", "memory_log"],  # 6, 7, 8
    "model": ["data_storage", "tech_stack"],  # model_role of 1, 9, 12
    "interaction": ["orchestration", "security"],  # 11, 13
    "evaluation": ["scalability", "test_plan"],  # 14 + the test plan annex
}
# The approval gate a build's interaction policy allows: never looser than how the owner chose to work.
GATES_FOR_POLICY = {"step_by_step": ["always"], "milestone": ["checkpoint", "always"],
                    "delegated_bounded": ["auto", "checkpoint", "always"]}
DEFAULT_GATE = {"step_by_step": "always", "milestone": "checkpoint", "delegated_bounded": "checkpoint"}
ALLOWED_SOURCES = ["task.input", "memory.phase_current", "skills"]
TOOL_PERMISSION = {"web_search": "network", "repo_write": "repo_write", "deploy": "deploy", "terminal": "repo_write",
                   "repo_read": None, "memory_search": None}
TECH_STACK = "Python 3.11, FastAPI, SQLAlchemy 2, Pydantic 2, app/gateway"

# What an owner may change, by branch: path -> kind. Anything else is the system's (shown, not editable).
EDITABLE: dict[str, dict[str, str]] = {
    "identity": {"agent_core.description": "text", "agent_core.capabilities": "list", "agent_core.name": "name"},
    "io_actions": {"io_contract.input_example": "text", "io_contract.expected_output": "text",
                   "action_engine.actions": "list"},
    "knowledge": {"knowledge_boundary.allowed_sources": "sources"},
    "tools": {"tools_plugins.tools": "tools", "external_integrations.services": "list"},
    "memory": {"memory.long_term_sources": "list"},
    "model": {"agent_core.model_role": "role"},
    "interaction": {"orchestration.approval_gate": "gate", "security.permissions.network": "bool",
                    "security.permissions.repo_write": "bool", "security.permissions.deploy": "bool",
                    "security.budget.max_cost": "money", "security.data_classification": "classification"},
    "evaluation": {"scalability.evaluation": "list"},
}
MAX_LIST_ITEMS = 20
MAX_ITEM_CHARS = 300
TEST_TEXT = {
    "edge_input": {"fa": "یک ورودی خالی یا خیلی کوتاه", "en": "an empty or very short input", "tr": "boş ya da çok kısa bir girdi"},
    "edge_expect": {"fa": "به‌جای حدس زدن، چیزی را که کم است می‌پرسد", "en": "asks for what is missing instead of guessing",
                    "tr": "tahmin etmek yerine eksik olanı sorar"},
    "failure_input": {"fa": "درخواستی بیرون از کارش یا خلاف محدودیت‌ها", "en": "a request outside its job or against its limits",
                      "tr": "işinin dışında ya da sınırlarına aykırı bir istek"},
    "failure_expect": {"fa": "رد می‌کند و دلیلش را می‌گوید؛ کاری بیرون از محدودیت‌ها نمی‌کند",
                       "en": "refuses and says why; does nothing outside its limits",
                       "tr": "reddeder ve nedenini söyler; sınırlarının dışında bir şey yapmaz"},
}


def _loc(table: dict[str, str], locale: str) -> str:
    return table.get(locale) or table["en"]


def _value(fields: dict[str, Any], name: str) -> Any:
    return (fields.get(name) or {}).get("value")


def _slug(text: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:60]
    return slug if re.fullmatch(r"[a-z][a-z0-9_-]{1,99}", slug or "") else fallback


def _money(text: Any, default: float = 1.0) -> float:
    m = re.search(r"\d+(?:[.,]\d+)?", str(text or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")))
    try:
        value = float(m.group(0).replace(",", ".")) if m else default
    except ValueError:
        value = default
    return value if 0 < value <= 10_000 else default


def _get(blueprint: dict[str, Any], path: str) -> Any:
    node: Any = blueprint
    for part in path.split("."):
        node = node.get(part) if isinstance(node, dict) else None
    return node


def _set(blueprint: dict[str, Any], path: str, value: Any) -> None:
    *parents, leaf = path.split(".")
    node = blueprint
    for part in parents:
        node = node.setdefault(part, {})
    node[leaf] = value


# ---------- the deterministic draft ----------


def draft_from_brief(fs: FactorySession, fields: dict[str, Any]) -> dict[str, Any]:
    """The 14-section Blueprint an approved brief implies, with the system's fixed choices filled in."""
    name = _slug(fs.title or "", f"agent-{fs.id}")
    goal = _value(fields, "goal") or ""
    expected = _value(fields, "expected_outputs") or ""
    coding = re.search(r"\b(code|coding|program|script)\b|کد|برنامه‌نویس|kod", f"{goal} {expected}", re.IGNORECASE)
    criteria = _value(fields, "acceptance_criteria")
    example = _value(fields, "input_examples")
    loc = fs.locale
    return {
        "agent_core": {"name": name, "description": goal, "model_role": "coding" if coding else "research",
                       "agent_type": "specialist", "capabilities": [name]},
        "knowledge_boundary": {"allowed_sources": ["task.input", "skills"], "general_llm_knowledge": False},
        "action_engine": {"actions": [name]},
        "tools_plugins": {"tools": []},
        "memory": {"short_term": True, "long_term_sources": []},
        "observation": {"record": ["runevent"]},
        "memory_log": {"enabled": True},
        "data_storage": {"backend": "postgres_pgvector"},
        "external_integrations": {"services": []},
        "orchestration": {"via_orchestrator": True, "approval_gate": DEFAULT_GATE.get(fs.interaction_policy, "always")},
        "tech_stack": {"stack": TECH_STACK},
        "security": {"permissions": {"network": False, "repo_write": False, "deploy": False},
                     "budget": {"max_cost": _money(_value(fields, "budget"))}, "data_classification": "internal"},
        "scalability": {"deployment": "docker_compose", "evaluation": [criteria] if criteria else []},
        "io_contract": {"input_example": example, "expected_output": expected or None,
                        "target_user": _value(fields, "target_user"), "problem": _value(fields, "problem")},
        "test_plan": [
            {"kind": "normal", "input": example, "expect": expected or None},
            {"kind": "edge", "input": _loc(TEST_TEXT["edge_input"], loc), "expect": _loc(TEST_TEXT["edge_expect"], loc)},
            {"kind": "failure", "input": _loc(TEST_TEXT["failure_input"], loc),
             "expect": _loc(TEST_TEXT["failure_expect"], loc)},
        ],
    }


def check(fs: FactorySession, blueprint: dict[str, Any]) -> list[str]:
    """Every issue of this version: the standard's validator, then the design rules."""
    issues = validate_blueprint(blueprint)
    permissions = _get(blueprint, "security.permissions") or {}
    for tool in _get(blueprint, "tools_plugins.tools") or []:
        needed = TOOL_PERMISSION.get(tool)
        if needed and not permissions.get(needed):
            issues.append(f"tools_plugins.tools: '{tool}' needs security.permissions.{needed}")
    gate = _get(blueprint, "orchestration.approval_gate")
    if gate not in GATES_FOR_POLICY.get(fs.interaction_policy, ["always"]):
        issues.append(f"orchestration.approval_gate: '{gate}' is looser than this build's interaction policy "
                      f"({fs.interaction_policy})")
    if not _get(blueprint, "io_contract.expected_output"):
        issues.append("io_contract.expected_output: required")
    return issues


def branch_of(issue: str) -> str | None:
    section = issue.split(".", 1)[0].split(":", 1)[0]
    if issue.startswith("agent_core.model_role"):
        return "model"
    return next((b for b, sections in BRANCHES.items() if section in sections), None)


# ---------- versions ----------


def current(db: Session, fs: FactorySession) -> BlueprintRevision | None:
    return db.scalar(select(BlueprintRevision).where(BlueprintRevision.session_id == fs.id)
                     .order_by(BlueprintRevision.version.desc()).limit(1))


def _new(db: Session, fs: FactorySession, blueprint: dict[str, Any], branches: dict[str, Any], *, brief_version: int,
         actor: User, reason: str) -> BlueprintRevision:
    latest = db.scalar(select(func.max(BlueprintRevision.version)).where(BlueprintRevision.session_id == fs.id)) or 0
    revision = BlueprintRevision(session_id=fs.id, version=latest + 1, brief_version=brief_version,
                                 schema_version=SCHEMA_VERSION, blueprint=blueprint, branches=branches,
                                 issues=check(fs, blueprint), created_by=actor.id, reason=reason)
    db.add(revision)
    return revision


def draft(db: Session, fs: FactorySession, brief_version: int, fields: dict[str, Any], actor: User) -> BlueprintRevision:
    """Called in the brief approval's transaction (app/factory.py::approve): the first draft of this brief version."""
    branches = {b: {"status": "proposed", "source": "system"} for b in BRANCHES}
    revision = _new(db, fs, draft_from_brief(fs, fields), branches, brief_version=brief_version, actor=actor,
                    reason="drafted")
    record_factory_event(db, fs.id, "design.drafted", actor.id, blueprint_version=revision.version,
                         brief_version=brief_version, issues=len(revision.issues))
    return revision


def _approved_brief_version(db: Session, fs: FactorySession) -> int | None:
    approval = db.scalar(select(Approval).where(Approval.session_id == fs.id, Approval.resource == "brief",
                                                Approval.invalidated_at.is_(None)).order_by(Approval.id.desc()).limit(1))
    return approval.resource_version if approval else None


def _invalidate(db: Session, fs: FactorySession, resource: str, reason: str) -> int | None:
    approval = db.scalar(select(Approval).where(Approval.session_id == fs.id, Approval.resource == resource,
                                                Approval.invalidated_at.is_(None)).order_by(Approval.id.desc()).limit(1))
    if approval is None:
        return None
    approval.invalidated_at, approval.invalidated_reason = utcnow(), reason
    return approval.id


def _stage_after(fs: FactorySession, revision: BlueprintRevision) -> FactoryStage:
    ready = not revision.issues and all(b["status"] == "confirmed" for b in revision.branches.values())
    return S.AWAITING_DESIGN_APPROVAL if ready else S.DESIGNING


def _begin(db: Session, owner: User, session_id: int, request_id: str, expected_revision: int, action: str
           ) -> tuple[FactorySession, BlueprintRevision] | tuple[FactorySession, None]:
    fs = factory.load_session(db, session_id, owner, lock=True)
    if not factory._claim_request(db, owner, request_id, fs.id):
        db.rollback()
        return fs, None
    factory._check(fs, expected_revision, action)
    if fs.stage not in (S.DESIGNING.value, S.AWAITING_DESIGN_APPROVAL.value):
        raise AppError(409, "stage_not_allowed", f"The design can't change while the build is {fs.stage}",
                       stage=fs.stage, action=action)
    revision = current(db, fs)
    if revision is None:
        raise AppError(409, "stage_not_allowed", "There is no design yet", stage=fs.stage, action=action)
    return fs, revision


def _parse(kind: str, value: Any, path: str) -> Any:
    def bad(why: str) -> AppError:
        return AppError(422, "design_value_invalid", f"{path}: {why}", path=path)

    if kind in ("text",):
        text = str(value or "").strip()
        if not text or len(text) > factory.MAX_ANSWER_CHARS:
            raise bad("a text of 1 to 4000 characters")
        if storable.problem(text):
            raise bad("text that can be stored")
        return text
    if kind == "name":
        text = str(value or "").strip()
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,99}", text):
            raise bad("lowercase letters, digits, - or _, starting with a letter")
        return text
    if kind in ("list", "sources", "tools"):
        items = value if isinstance(value, list) else [p for p in re.split(r"[,،\n]", str(value or ""))]
        items = [str(i).strip() for i in items if str(i).strip()]
        if len(items) > MAX_LIST_ITEMS or any(len(i) > MAX_ITEM_CHARS or storable.problem(i) for i in items):
            raise bad(f"at most {MAX_LIST_ITEMS} items of at most {MAX_ITEM_CHARS} characters")
        allowed = {"sources": ALLOWED_SOURCES, "tools": sorted(KNOWN_TOOLS)}.get(kind)
        if allowed is not None and (unknown := sorted(set(items) - set(allowed))):
            raise bad(f"unknown values {unknown}")
        return list(dict.fromkeys(items))
    if kind == "bool":
        if not isinstance(value, bool):
            raise bad("true or false")
        return value
    if kind == "money":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not (0 < float(value) <= 10_000):
            raise bad("a number above 0, at most 10000")
        return float(value)
    choices = {"role": sorted(MODEL_ROLES), "gate": ["auto", "checkpoint", "always"],
               "classification": ["public", "internal", "sensitive"]}[kind]
    if value not in choices:
        raise bad(f"one of {choices}")
    return value


# ---------- the owner's actions ----------


def edit(db: Session, owner: User, session_id: int, *, branch: str, changes: dict[str, Any], expected_revision: int,
         request_id: str) -> tuple[FactorySession, bool]:
    fs, revision = _begin(db, owner, session_id, request_id, expected_revision, "design_edit")
    if revision is None:
        return fs, False
    allowed = EDITABLE.get(branch)
    if allowed is None:
        raise AppError(422, "unknown_branch", f"Unknown branch '{branch}'", branch=branch)
    if not changes:
        raise AppError(422, "design_value_invalid", "Nothing to change", path=branch)
    blueprint = copy.deepcopy(revision.blueprint)
    for path, value in changes.items():
        if path not in allowed:
            raise AppError(422, "design_value_invalid", f"{path}: not editable in branch '{branch}'", path=path)
        _set(blueprint, path, _parse(allowed[path], value, path))
    if branch == "identity" and "agent_core.capabilities" in changes:
        # The actions follow the capabilities unless the owner set them themselves.
        if revision.branches.get("io_actions", {}).get("source") != "user":
            _set(blueprint, "action_engine.actions", list(blueprint["agent_core"]["capabilities"]))
    branches = copy.deepcopy(revision.branches)
    branches[branch] = {"status": "proposed", "source": "user"}
    new = _new(db, fs, blueprint, branches, brief_version=revision.brief_version, actor=owner, reason="edited")
    invalidated = _invalidate(db, fs, "blueprint", "design_changed")
    factory._bump(fs, _stage_after(fs, new))
    record_factory_event(db, fs.id, "design.edited", owner.id, branch=branch, paths=sorted(changes),
                         blueprint_version=new.version, **({"approval_invalidated": invalidated} if invalidated else {}))
    return fs, factory._commit(db)


def confirm_branch(db: Session, owner: User, session_id: int, *, branch: str, blueprint_version: int,
                   expected_revision: int, request_id: str) -> tuple[FactorySession, bool]:
    """The owner confirms one branch of exactly this version. A branch with an issue can't be confirmed."""
    fs, revision = _begin(db, owner, session_id, request_id, expected_revision, "design_confirm")
    if revision is None:
        return fs, False
    if branch not in BRANCHES:
        raise AppError(422, "unknown_branch", f"Unknown branch '{branch}'", branch=branch)
    if revision.version != blueprint_version:
        raise AppError(409, "stale_version", "The design changed since you read it", current_version=revision.version)
    if blocking := [i for i in revision.issues if branch_of(i) == branch]:
        raise AppError(409, "branch_incomplete", f"Branch '{branch}' has open issues", branch=branch, issues=blocking)
    branches = copy.deepcopy(revision.branches)
    branches[branch] = {**branches.get(branch, {}), "status": "confirmed", "confirmed_by": owner.id}
    new = _new(db, fs, copy.deepcopy(revision.blueprint), branches, brief_version=revision.brief_version, actor=owner,
               reason="branch_confirmed")
    factory._bump(fs, _stage_after(fs, new))
    record_factory_event(db, fs.id, "design.branch_confirmed", owner.id, branch=branch, blueprint_version=new.version)
    return fs, factory._commit(db)


def revert(db: Session, owner: User, session_id: int, *, to_version: int, expected_revision: int, request_id: str
           ) -> tuple[FactorySession, bool]:
    """Undo: an earlier version of this brief's design comes back as a new version (history is never rewritten)."""
    fs, revision = _begin(db, owner, session_id, request_id, expected_revision, "design_revert")
    if revision is None:
        return fs, False
    old = db.scalar(select(BlueprintRevision).where(BlueprintRevision.session_id == fs.id,
                                                    BlueprintRevision.version == to_version))
    if old is None or old.brief_version != revision.brief_version or old.version >= revision.version:
        raise AppError(409, "stale_version", "That version can't be restored here", current_version=revision.version)
    new = _new(db, fs, copy.deepcopy(old.blueprint), copy.deepcopy(old.branches), brief_version=old.brief_version,
               actor=owner, reason="reverted")
    invalidated = _invalidate(db, fs, "blueprint", "design_changed")
    factory._bump(fs, _stage_after(fs, new))
    record_factory_event(db, fs.id, "design.reverted", owner.id, to_version=to_version, blueprint_version=new.version,
                         **({"approval_invalidated": invalidated} if invalidated else {}))
    return fs, factory._commit(db)


def approve(db: Session, owner: User, session_id: int, *, blueprint_version: int, expected_revision: int,
            request_id: str) -> tuple[FactorySession, bool]:
    """The owner approves the design and its permissions: exactly this version, made from the brief version that is
    approved now, every branch confirmed, no issue."""
    fs = factory.load_session(db, session_id, owner, lock=True)
    if not factory._claim_request(db, owner, request_id, fs.id):
        db.rollback()
        return fs, False
    factory._check(fs, expected_revision, "design_approve")
    if fs.stage != S.AWAITING_DESIGN_APPROVAL.value:
        raise AppError(409, "stage_not_allowed", f"Nothing to approve while the build is {fs.stage}",
                       stage=fs.stage, action="design_approve")
    revision = current(db, fs)
    if revision.version != blueprint_version:
        raise AppError(409, "stale_version", "The design changed since you read it", current_version=revision.version)
    if revision.brief_version != _approved_brief_version(db, fs):
        raise AppError(409, "stale_version", "The design was made from a brief that is no longer the approved one",
                       current_version=revision.version)
    if revision.issues or not all(b["status"] == "confirmed" for b in revision.branches.values()):
        raise AppError(409, "design_incomplete", "Every branch must be confirmed and free of issues",
                       issues=revision.issues)
    db.add(Approval(session_id=fs.id, actor_id=owner.id, stage=S.AWAITING_DESIGN_APPROVAL.value, resource="blueprint",
                    resource_version=revision.version, scope=sorted(BRANCHES)))
    factory._bump(fs, S.BUILDING)
    record_factory_event(db, fs.id, "design.approved", owner.id, blueprint_version=revision.version,
                         brief_version=revision.brief_version,
                         permissions=_get(revision.blueprint, "security.permissions"))
    return fs, factory._commit(db)


# ---------- improve with AI (the only model call, on request) ----------


class TestCaseOut(BaseModel):
    kind: str = Field(pattern=r"^(normal|edge|failure)$")
    input: str = Field(max_length=MAX_ITEM_CHARS)
    expect: str = Field(max_length=MAX_ITEM_CHARS)


class AssistOut(storable.StorableOut):
    description: str = Field(max_length=1000)
    capabilities: list[str] = Field(min_length=1, max_length=5)
    test_cases: list[TestCaseOut] = Field(min_length=3, max_length=6)


def _assist_prompt(fs: FactorySession, fields: dict[str, Any]) -> str:
    lines = [f"{name}: {_value(fields, name)}" for name in ("goal", "target_user", "problem", "input_examples",
                                                            "expected_outputs", "acceptance_criteria", "constraints")
             if _value(fields, name)]
    lines.append("Reply language: " + {"fa": "Persian (fa)", "en": "English (en)", "tr": "Turkish (tr)"}[fs.locale])
    return "Approved brief of the agent to design:\n" + "\n".join(lines)


def assist(db: Session, gateway: Gateway, owner: User, session_id: int, *, expected_revision: int, request_id: str
           ) -> tuple[FactorySession, bool]:
    """One model call, asked for by the owner, within the build's budget. Nothing is written while it runs (d19); if
    the build changed meanwhile the paid suggestion is only recorded, not applied (409)."""
    fs = factory.load_session(db, session_id, owner)
    factory._check(fs, expected_revision, "design_assist")
    if fs.stage not in (S.DESIGNING.value, S.AWAITING_DESIGN_APPROVAL.value):
        raise AppError(409, "stage_not_allowed", f"No design to improve while the build is {fs.stage}",
                       stage=fs.stage, action="design_assist")
    revision = current(db, fs)
    brief = factory.current_brief(db, fs)
    db.rollback()  # nothing of this request is open while the model runs
    from app.manager import manager_agent  # local import: app.manager pulls in the whole planning stack

    system = context.system_prompt(db, manager_agent(db), ["design-assist"])
    response = gateway.call("cheap", system=system, user=_assist_prompt(fs, brief.fields), agent="manager",
                            json_schema=AssistOut.model_json_schema(), factory_session_id=fs.id)
    try:
        suggestion = AssistOut.model_validate(response.data)
    except ValidationError as e:
        raise AppError(422, "request_refused", f"invalid suggestion: {e}") from e

    fs = factory.load_session(db, session_id, owner, lock=True)
    if not factory._claim_request(db, owner, request_id, fs.id):
        db.rollback()
        return fs, False
    if fs.revision != expected_revision:
        record_factory_event(db, fs.id, "design.assist_discarded", owner.id, reason="build_changed")
        db.commit()
        raise AppError(409, "stale_revision", "The build changed while the suggestion was being made",
                       current_revision=fs.revision, expected_revision=expected_revision)
    blueprint = copy.deepcopy(revision.blueprint)
    blueprint["agent_core"]["description"] = suggestion.description
    capabilities = [_slug(c, "") or c for c in suggestion.capabilities]
    capabilities = [c for c in capabilities if re.fullmatch(r"[a-z][a-z0-9_-]{1,99}", c)] or blueprint["agent_core"]["capabilities"]
    blueprint["agent_core"]["capabilities"] = capabilities
    blueprint["action_engine"]["actions"] = list(capabilities)
    blueprint["test_plan"] = [t.model_dump() for t in suggestion.test_cases]
    branches = copy.deepcopy(revision.branches)
    for b in ("identity", "io_actions", "evaluation"):
        branches[b] = {"status": "proposed", "source": "model"}
    new = _new(db, fs, blueprint, branches, brief_version=revision.brief_version, actor=owner, reason="assisted")
    _invalidate(db, fs, "blueprint", "design_changed")
    factory._bump(fs, _stage_after(fs, new))
    record_factory_event(db, fs.id, "design.assisted", owner.id, blueprint_version=new.version,
                         cost_usd=response.cost_usd)
    return fs, factory._commit(db)


# ---------- what the canvas shows ----------


def view(db: Session, fs: FactorySession) -> dict[str, Any] | None:
    revision = current(db, fs)
    if revision is None:
        return None
    bp = revision.blueprint
    branches = []
    for key, sections in BRANCHES.items():
        state = revision.branches.get(key, {"status": "proposed", "source": "system"})
        issues = [i for i in revision.issues if branch_of(i) == key]
        values = {path: _get(bp, path) for path in EDITABLE[key]}
        if key == "model":
            values.update({"data_storage.backend": _get(bp, "data_storage.backend"),
                           "scalability.deployment": _get(bp, "scalability.deployment")})
        if key == "memory":
            values.update({"memory.short_term": _get(bp, "memory.short_term"),
                           "observation.record": _get(bp, "observation.record")})
        if key == "evaluation":
            values["test_plan"] = _get(bp, "test_plan")
        branches.append({"key": key, "sections": sections, "status": "incomplete" if issues else state["status"],
                         "source": state.get("source"), "issues": issues, "values": values,
                         "editable": EDITABLE[key]})
    history = db.execute(
        select(BlueprintRevision.version, BlueprintRevision.reason)
        .where(BlueprintRevision.session_id == fs.id, BlueprintRevision.brief_version == revision.brief_version)
        .order_by(BlueprintRevision.version.desc()).limit(10)).all()
    return {
        "version": revision.version, "brief_version": revision.brief_version, "schema_version": revision.schema_version,
        "reason": revision.reason, "issues": revision.issues, "branches": branches, "blueprint": bp,
        "history": [{"version": v, "reason": r} for v, r in history],
        "can_approve": fs.stage == S.AWAITING_DESIGN_APPROVAL.value and not revision.issues,
        "budget": {"limit_usd": fs.budget_usd, "spent_usd": round(build_spent_usd(db, fs.id), 6),
                   "held_usd": round(build_held_usd(db, fs.id), 6)},
        "gates_allowed": GATES_FOR_POLICY.get(fs.interaction_policy, ["always"]),
    }
