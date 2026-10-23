"""Workflows: agents connected on a canvas (PR6 of issue #2; docs/HUMAN_GUIDED_FACTORY.md §15; decisions d40, d41).

- A workflow is versioned. Every semantic change (a node, an edge, a mapping, a model, a tool) saves a new immutable
  WorkflowVersion with the validator's issues; moving a node only changes the workflow's layout (display, no version,
  no approval lost). Saving never runs anything.
- Four kinds of edge, each checked in code before it is accepted as valid: data (an output field of one agent into an
  input field of another, with an explicit mapping, never a silent conversion), model (a model role as the agent's
  configuration), tool (only a tool the agent has, whose permission is granted and that the Tool Gateway implements),
  knowledge (an allowed source). A drawn edge grants nothing at run time: the runtime checks it all again.
- Nodes pin an exact agent version. A newer version never changes a workflow or its earlier runs.
- Run (d40) turns an approved version into the project's tasks through the manager's own plan path (app/manager.py::
  _add_plan): dependencies from the data edges, a missing agent is a BLOCKED task, the plan waits for the owner's
  approval, and execution goes through checkpoints and bounded runs. Agents talk only through task dependencies and
  their results, never directly.
"""

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import design, factory, registry, runtime, settings_layers, storable
from app.errors import AppError, not_found
from app.events import record_event
from app.manager import PlanIn, PlanStepIn, ensure_active, stage_plan
from app.models import (
    Agent, AgentVersion, BlueprintRevision, Project, Task, User, Workflow, WorkflowVersion, utcnow,
)
from app.registry import KNOWN_TOOLS, MODEL_ROLES
from app.storable import Int32, StorableIn, limit

MAX_NODES = 30
MAX_EDGES = 60
MAX_MAPPINGS = 10
NODE_TYPES = ("agent", "model", "tool", "source")
EDGE_KINDS = ("data", "model", "tool", "knowledge")
ID_PATTERN = r"^[a-z][a-z0-9_-]{0,39}$"
# What every agent returns (app/manager.py::TaskRunOut) and takes (its task and an optional context), by field: type.
OUTPUT_FIELDS = {"summary": "text", "findings": "list", "lesson": "text", "next": "text"}
INPUT_FIELDS = {"task": "text", "context": "text"}
REQUIRED_INPUTS = ("task",)
# The ports of an edge kind: (the source node type and its port, the target port on the agent).
PORTS = {"data": ("agent", "result", "input"), "model": ("model", "out", "model"),
         "tool": ("tool", "out", "tools"), "knowledge": ("source", "out", "knowledge")}
USABLE_VERSION_STATES = ("passed", "active", "retired")  # a version that passed its evaluation
ISSUE_CODES = {
    "bad_node_id", "bad_edge_id", "duplicate_id", "unknown_agent", "edge_unknown_node", "edge_wrong_ports", "edge_self",
    "mapping_missing", "mapping_unknown_field", "mapping_type_mismatch", "mapping_target_twice",
    "required_input_missing", "cycle", "tool_unknown", "tool_not_granted", "tool_unavailable",
    "knowledge_not_allowed", "model_role_invalid", "model_twice", "no_agent", "unlinked_node",
}
RUN_RISK = "low"


# ---------- what a request may carry (shape only: meaning is the validator's, and it saves the issues) ----------


class NodeIn(StorableIn):
    id: str = Field(max_length=40)
    type: Literal["agent", "model", "tool", "source"]
    agent: str | None = Field(default=None, max_length=limit(Agent, "name"))
    version: Int32 | None = None
    title: str | None = Field(default=None, max_length=limit(Agent, "name"))
    task: str | None = Field(default=None, max_length=1000)
    role: str | None = Field(default=None, max_length=50)
    tool: str | None = Field(default=None, max_length=50)
    source: str | None = Field(default=None, max_length=50)


class PortIn(StorableIn):
    node: str = Field(max_length=40)
    port: str = Field(max_length=20)


class MappingIn(StorableIn):
    source: str = Field(max_length=30)
    target: str = Field(max_length=30)
    convert: Literal["join"] | None = None


class EdgeIn(StorableIn):
    id: str = Field(max_length=40)
    kind: Literal["data", "model", "tool", "knowledge"]
    from_: PortIn = Field(alias="from")
    to: PortIn
    mapping: list[MappingIn] = Field(default_factory=list, max_length=MAX_MAPPINGS)

    model_config = {"populate_by_name": True}


class GraphIn(StorableIn):
    nodes: list[NodeIn] = Field(max_length=MAX_NODES)
    edges: list[EdgeIn] = Field(max_length=MAX_EDGES)

    def dump(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        nodes = [n.model_dump(exclude_none=True) for n in self.nodes]
        edges = [e.model_dump(by_alias=True, exclude_none=True) for e in self.edges]
        return nodes, edges


# ---------- the agents a node may pin ----------


def resolve_agent(db: Session, owner: User, name: str | None, version: int | None) -> dict[str, Any] | None:
    """What the pinned agent version is: its tools, permissions and allowed sources — or None if this owner may not
    pin it (not there, not a version that passed, or someone else's)."""
    if not name or version is None:
        return None
    built = db.scalar(select(AgentVersion).where(AgentVersion.agent_name == name, AgentVersion.version == version))
    if built is not None:
        from app.models import FactorySession

        if built.state not in USABLE_VERSION_STATES or db.get(FactorySession, built.session_id).owner_id != owner.id:
            return None
        blueprint = db.scalar(select(BlueprintRevision).where(BlueprintRevision.session_id == built.session_id,
                                                              BlueprintRevision.version == built.blueprint_version))
        sources = ((blueprint.blueprint.get("knowledge_boundary") or {}).get("allowed_sources") or []) if blueprint else []
        return {"tools": list(built.spec.get("tools") or []), "permissions": dict(built.spec.get("permissions") or {}),
                "sources": list(sources), "built": True}
    if db.scalar(select(AgentVersion.id).where(AgentVersion.agent_name == name)) is not None:
        return None  # a built agent's other versions: only the ones that passed are pinnable, found above
    agent = db.scalar(select(Agent).where(Agent.name == name, Agent.active))
    if agent is None or agent.version != version:
        return None
    return {"tools": list(agent.tools or []), "permissions": dict(agent.permissions or {}),
            "sources": list(design.ALLOWED_SOURCES), "built": False, "pin": fingerprint(agent)}


def fingerprint(agent: Agent) -> str:
    """What a registry agent is, beyond its number (the registry rewrites a row without bumping the version)."""
    spec = [agent.model_role, agent.instructions, sorted(agent.tools or []), agent.permissions or {},
            sorted(agent.skills or [])]
    return hashlib.sha256(json.dumps(spec, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def catalog(db: Session, owner: User) -> dict[str, Any]:
    """What the canvas can offer: the agents this owner may pin (with their ports' fields), roles, tools, sources."""
    from app.models import FactorySession

    agents: dict[tuple[str, int], dict[str, Any]] = {}
    built_names = set(db.scalars(select(AgentVersion.agent_name).distinct()))
    for a in db.scalars(select(Agent).where(Agent.active).order_by(Agent.name)):
        if a.name not in built_names:
            agents[(a.name, a.version)] = {"name": a.name, "version": a.version, "state": "registry",
                                           "tools": list(a.tools or []), "description": a.description}
    rows = db.execute(select(AgentVersion, FactorySession.owner_id).join(
        FactorySession, FactorySession.id == AgentVersion.session_id)
        .where(AgentVersion.state.in_(USABLE_VERSION_STATES)).order_by(AgentVersion.agent_name, AgentVersion.version))
    for v, owner_id in rows:
        if owner_id == owner.id:
            agents[(v.agent_name, v.version)] = {"name": v.agent_name, "version": v.version, "state": v.state,
                                                 "tools": list(v.spec.get("tools") or []),
                                                 "description": v.spec.get("description", "")}
    return {"agents": list(agents.values()), "roles": sorted(MODEL_ROLES), "tools": sorted(KNOWN_TOOLS),
            "implemented_tools": sorted(runtime.TOOLS), "sources": list(design.ALLOWED_SOURCES),
            "output_fields": OUTPUT_FIELDS, "input_fields": INPUT_FIELDS, "required_inputs": list(REQUIRED_INPUTS)}


# ---------- the validator ----------


def validate(db: Session, owner: User, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every reason this graph can't run, as {code, node|edge, ...}. Deterministic; the same graph, the same issues."""
    issues: list[dict[str, Any]] = []

    def issue(code: str, **detail: Any) -> None:
        assert code in ISSUE_CODES, code
        issues.append({"code": code, **detail})

    by_id: dict[str, dict[str, Any]] = {}
    for n in nodes:
        if not re.fullmatch(ID_PATTERN, n["id"]):
            issue("bad_node_id", node=n["id"])
        if n["id"] in by_id:
            issue("duplicate_id", node=n["id"])
        by_id[n["id"]] = n
    edge_ids: set[str] = set()
    for e in edges:
        if not re.fullmatch(ID_PATTERN, e["id"]):
            issue("bad_edge_id", edge=e["id"])
        elif e["id"] in edge_ids:
            issue("duplicate_id", edge=e["id"])
        edge_ids.add(e["id"])

    agents = {n["id"]: n for n in nodes if n["type"] == "agent"}
    resolved = {}
    for node_id, n in agents.items():
        info = resolve_agent(db, owner, n.get("agent"), n.get("version"))
        if info is None:
            issue("unknown_agent", node=node_id, agent=n.get("agent"), version=n.get("version"))
        resolved[node_id] = info
    if not agents:
        issue("no_agent")
    for n in nodes:
        if n["type"] == "model" and n.get("role") not in MODEL_ROLES:
            issue("model_role_invalid", node=n["id"], role=n.get("role"))

    incoming_data: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    models_of: dict[str, int] = {}
    linked: set[str] = set()
    for e in edges:
        src, dst = by_id.get(e["from"]["node"]), by_id.get(e["to"]["node"])
        if src is None or dst is None:
            issue("edge_unknown_node", edge=e["id"])
            continue
        linked |= {src["id"], dst["id"]}
        if src["id"] == dst["id"]:
            issue("edge_self", edge=e["id"])
            continue
        from_type, from_port, to_port = PORTS[e["kind"]]
        if (src["type"], e["from"]["port"], dst["type"], e["to"]["port"]) != (from_type, from_port, "agent", to_port):
            issue("edge_wrong_ports", edge=e["id"], kind=e["kind"])
            continue
        info = resolved.get(dst["id"])
        if e["kind"] == "data":
            _check_mapping(e, issue)
            incoming_data.setdefault(dst["id"], []).append((e, src))
        elif e["kind"] == "model":
            models_of[dst["id"]] = models_of.get(dst["id"], 0) + 1
            if models_of[dst["id"]] == 2:
                issue("model_twice", node=dst["id"])
        elif e["kind"] == "tool" and info is not None:
            tool = src.get("tool")
            permission = design.TOOL_PERMISSION.get(tool)
            if tool not in KNOWN_TOOLS or tool not in info["tools"]:
                issue("tool_unknown", edge=e["id"], tool=tool)
            elif tool not in runtime.TOOLS:
                issue("tool_unavailable", edge=e["id"], tool=tool)
            elif permission and not info["permissions"].get(permission):
                issue("tool_not_granted", edge=e["id"], tool=tool, permission=permission)
        elif e["kind"] == "knowledge" and info is not None and src.get("source") not in info["sources"]:
            issue("knowledge_not_allowed", edge=e["id"], source=src.get("source"))

    for node_id, n in agents.items():
        mapped = {m["target"] for e, _ in incoming_data.get(node_id, []) for m in e.get("mapping", [])}
        for field in REQUIRED_INPUTS:
            if field not in mapped and not (field == "task" and (n.get("task") or "").strip()):
                issue("required_input_missing", node=node_id, field=field)
        targets = [m["target"] for e, _ in incoming_data.get(node_id, []) for m in e.get("mapping", [])]
        for field in {t for t in targets if targets.count(t) > 1}:
            issue("mapping_target_twice", node=node_id, field=field)
    for n in nodes:
        if n["type"] != "agent" and n["id"] not in linked:
            issue("unlinked_node", node=n["id"])

    cycle = _cycle(agents, edges)
    if cycle:
        issue("cycle", nodes=cycle)
    return issues


def _check_mapping(e: dict[str, Any], issue) -> None:
    mapping = e.get("mapping") or []
    if not mapping:
        issue("mapping_missing", edge=e["id"])
    for m in mapping:
        if m["source"] not in OUTPUT_FIELDS or m["target"] not in INPUT_FIELDS:
            issue("mapping_unknown_field", edge=e["id"], source=m["source"], target=m["target"])
            continue
        have, want, convert = OUTPUT_FIELDS[m["source"]], INPUT_FIELDS[m["target"]], m.get("convert")
        if have == want and convert is None:
            continue
        if have == "list" and want == "text" and convert == "join":
            continue
        # The suggestion: the one explicit conversion that would make it fit (never applied silently).
        issue("mapping_type_mismatch", edge=e["id"], source=m["source"], target=m["target"], have=have, want=want,
              suggest="join" if (have, want) == ("list", "text") else None)


def _cycle(agents: dict[str, Any], edges: list[dict[str, Any]]) -> list[str]:
    """The agent nodes that sit on or behind a dependency cycle (data edges), or []."""
    deps = {a: set() for a in agents}
    for e in edges:
        if e["kind"] == "data" and e["from"]["node"] in agents and e["to"]["node"] in agents:
            deps[e["to"]["node"]].add(e["from"]["node"])
    left = {a: set(d) for a, d in deps.items()}
    while True:
        free = [a for a, d in left.items() if not d]
        if not free:
            break
        for a in free:
            del left[a]
        for d in left.values():
            d.difference_update(free)
    return sorted(left)


# ---------- changing a workflow ----------


def load(db: Session, workflow_id: int, owner: User, *, lock: bool = False) -> Workflow:
    query = select(Workflow).where(Workflow.id == workflow_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    workflow = db.scalar(query)
    if workflow is None or workflow.owner_id != owner.id:
        raise not_found("workflow")
    return workflow


def versions_of(db: Session, workflow: Workflow) -> list[WorkflowVersion]:
    return list(db.scalars(select(WorkflowVersion).where(WorkflowVersion.workflow_id == workflow.id)
                           .order_by(WorkflowVersion.version.desc())))


def latest(db: Session, workflow: Workflow) -> WorkflowVersion:
    return db.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_id == workflow.id)
                     .order_by(WorkflowVersion.version.desc()).limit(1))


def _check(workflow: Workflow, expected_revision: int) -> None:
    if workflow.revision != expected_revision:
        raise AppError(409, "stale_revision", "This workflow changed since you loaded it; reload and look again",
                       current_revision=workflow.revision, expected_revision=expected_revision)


def _bump(workflow: Workflow) -> None:
    workflow.revision += 1
    workflow.updated_at = utcnow()


def _by_request(db: Session, owner: User, request_id: str) -> Workflow | None:
    return db.scalar(select(Workflow).where(Workflow.owner_id == owner.id, Workflow.request_id == request_id))


def create(db: Session, owner: User, *, name: str, request_id: str) -> tuple[Workflow, bool]:
    """The request id is the workflow's own (unique per owner): the same request again returns that workflow."""
    if (existing := _by_request(db, owner, request_id)) is not None:
        return existing, False
    workflow = Workflow(owner_id=owner.id, name=name, request_id=request_id)
    db.add(workflow)
    try:
        db.flush()
        db.add(WorkflowVersion(workflow_id=workflow.id, version=1, nodes=[], edges=[],
                               issues=[{"code": "no_agent"}], created_by=owner.id))
        db.commit()
        return workflow, True
    except IntegrityError:  # a concurrent identical request won: this one is its replay
        db.rollback()
        return _by_request(db, owner, request_id), False


def save(db: Session, owner: User, workflow_id: int, graph: GraphIn, *, expected_revision: int, request_id: str
         ) -> tuple[Workflow, bool]:
    """A new draft version with its issues. A graph that means the same as the latest one changes nothing."""
    workflow = load(db, workflow_id, owner, lock=True)
    if not factory._claim_request(db, owner, request_id, None):
        db.rollback()
        return workflow, False
    _check(workflow, expected_revision)
    nodes, edges = graph.dump()
    for n in nodes:  # the server's own record of which registry agent this pinned (a client can't send one)
        info = resolve_agent(db, owner, n.get("agent"), n.get("version")) if n["type"] == "agent" else None
        if info is not None and info.get("pin"):
            n["pin"] = info["pin"]
    current = latest(db, workflow)
    if (nodes, edges) == (current.nodes, current.edges):
        db.commit()
        return workflow, True
    db.add(WorkflowVersion(workflow_id=workflow.id, version=current.version + 1, nodes=nodes, edges=edges,
                           issues=validate(db, owner, nodes, edges), created_by=owner.id))
    _bump(workflow)
    return workflow, factory._commit(db)


def set_layout(db: Session, owner: User, workflow_id: int, positions: dict[str, dict[str, float]]) -> Workflow:
    """Where nodes sit: display only. No version, no revision, no approval is touched."""
    workflow = load(db, workflow_id, owner, lock=True)
    ids = {n["id"] for n in latest(db, workflow).nodes}
    workflow.layout = {k: v for k, v in positions.items() if k in ids}
    db.commit()
    return workflow


def approve(db: Session, owner: User, workflow_id: int, *, version: int, expected_revision: int, request_id: str
            ) -> tuple[Workflow, bool]:
    workflow = load(db, workflow_id, owner, lock=True)
    if not factory._claim_request(db, owner, request_id, None):
        db.rollback()
        return workflow, False
    _check(workflow, expected_revision)
    current = latest(db, workflow)
    if current.version != version:
        raise AppError(409, "stale_version", "The workflow changed since you read it", current_version=current.version)
    issues = validate(db, owner, current.nodes, current.edges)  # again: an agent may have changed since it was saved
    if issues:
        raise AppError(409, "workflow_invalid", "The workflow has problems; fix them first", issues=issues)
    current.issues, current.approved_by, current.approved_at = [], owner.id, utcnow()
    _bump(workflow)
    return workflow, factory._commit(db)


def approved_version(db: Session, workflow: Workflow) -> WorkflowVersion | None:
    return db.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_id == workflow.id,
                                                   WorkflowVersion.approved_at.is_not(None))
                     .order_by(WorkflowVersion.version.desc()).limit(1))


# ---------- run: the approved version becomes the project's tasks ----------


def plan_of(db: Session, owner: User, version: WorkflowVersion) -> tuple[PlanIn, list[dict[str, Any]]]:
    """The manager's plan form of a workflow version, and per step its node. Agents are re-resolved now: a pinned
    version that is no longer what is active is refused (the workflow must be re-pinned), not run silently."""
    agents = [n for n in version.nodes if n["type"] == "agent"]
    order = {n["id"]: i for i, n in enumerate(agents)}
    depends: dict[str, set[int]] = {n["id"]: set() for n in agents}
    for e in version.edges:
        if e["kind"] == "data":
            depends[e["to"]["node"]].add(order[e["from"]["node"]])
    steps = []
    for n in agents:
        known = registry.agent_for(db, n["agent"], owner.id)
        if known is not None and (known.version != n["version"] or (n.get("pin") and n["pin"] != fingerprint(known))):
            raise AppError(409, "agent_version_changed",
                           f"'{n['agent']}' is version {known.version} now; the workflow pins version {n['version']}",
                           node=n["id"], agent=n["agent"], pinned=n["version"], active=known.version)
        steps.append(PlanStepIn(title=n.get("title") or n["agent"], agent=n["agent"],
                                goal=(n.get("task") or "").strip() or "Work from the mapped results of the earlier tasks.",
                                depends_on=sorted(depends[n["id"]])))
    plan = PlanIn(understanding=f"Workflow (version {version.version})", assumptions=[], steps=steps)
    return plan, agents


def run(db: Session, owner: User, workflow_id: int, *, version: int, project_id: int, request_id: str
        ) -> tuple[dict[str, Any], bool]:
    from app import auth

    workflow = load(db, workflow_id, owner)
    project = auth.own_project(db, project_id, owner)
    chosen = db.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_id == workflow.id,
                                                     WorkflowVersion.version == version))
    if chosen is None or chosen.approved_at is None:
        raise AppError(409, "workflow_not_approved", "Only an approved version can run", version=version)
    ensure_active(db, project, "workflow.run")
    if not factory._claim_request(db, owner, request_id, None):
        db.rollback()
        return {"duplicate": True}, False
    plan, agents = plan_of(db, owner, chosen)
    result = stage_plan(db, project, plan, int(settings_layers.resolve(db, project_id=project.id)["max_steps"]))
    ids = {n["id"]: tid for n, tid in zip(agents, result["task_ids"])}
    for n, tid in zip(agents, result["task_ids"]):
        task = db.get(Task, tid)
        mappings = [{"from_task": ids[e["from"]["node"]], "source": m["source"], "target": m["target"],
                     "convert": m.get("convert")}
                    for e in chosen.edges if e["kind"] == "data" and e["to"]["node"] == n["id"]
                    for m in e.get("mapping", [])]
        task.input = {**task.input, "workflow": {"id": workflow.id, "version": chosen.version, "node": n["id"],
                                                 "agent_version": n["version"]}, "mappings": mappings}
    record_event(db, project.id, "workflow.run", None, workflow_id=workflow.id, version=chosen.version,
                 task_ids=result["task_ids"])
    if not factory._commit(db):  # a concurrent identical request won: nothing of this one is kept
        return {"duplicate": True}, False
    return {**result, "workflow_id": workflow.id, "version": chosen.version}, True


# ---------- what the UI shows ----------


def view(db: Session, workflow: Workflow, owner: User) -> dict[str, Any]:
    current = latest(db, workflow)
    approved = approved_version(db, workflow)
    issues = [] if current.approved_at is not None else validate(db, owner, current.nodes, current.edges)
    return {
        "id": workflow.id, "name": workflow.name, "revision": workflow.revision, "layout": workflow.layout or {},
        "version": current.version, "nodes": current.nodes, "edges": current.edges,
        "issues": issues,
        "approved_version": approved.version if approved else None,
        "can_approve": current.approved_at is None and not issues,
        "approved": current.approved_at is not None,
        "history": [{"version": v.version, "approved": v.approved_at is not None, "created_at": v.created_at}
                    for v in versions_of(db, workflow)[:20]],
        "catalog": catalog(db, owner),
    }


def listing(db: Session, owner: User) -> list[dict[str, Any]]:
    workflows = list(db.scalars(select(Workflow).where(Workflow.owner_id == owner.id).order_by(Workflow.updated_at.desc())))
    by_workflow: dict[int, list[WorkflowVersion]] = {w.id: [] for w in workflows}
    for v in db.scalars(select(WorkflowVersion).where(WorkflowVersion.workflow_id.in_(by_workflow))
                        .order_by(WorkflowVersion.version.desc())):
        by_workflow[v.workflow_id].append(v)
    rows = []
    for w in workflows:
        versions = by_workflow[w.id]  # newest first
        approved = next((v for v in versions if v.approved_at is not None), None)
        rows.append({"id": w.id, "name": w.name, "revision": w.revision, "version": versions[0].version,
                     "agents": sum(1 for n in versions[0].nodes if n["type"] == "agent"),
                     "approved_version": approved.version if approved else None, "updated_at": w.updated_at})
    return rows


def mapped_inputs(db: Session, task: Task) -> dict[str, str]:
    """The values a workflow task's mappings take from the results of the tasks it depends on (context.task_context)."""
    values: dict[str, Any] = {}
    allowed = set(task.depends_on)  # task.input is the owner's to write: a mapping may name only a dependency
    for m in (task.input or {}).get("mappings") or []:
        if not isinstance(m, dict) or m.get("from_task") not in allowed:
            continue
        source = db.get(Task, m["from_task"])
        if source is None or source.project_id != task.project_id:
            continue
        value = (source.output or {}).get(m.get("source"))
        if value is None or not isinstance(m.get("target"), str):
            continue
        if isinstance(value, list):
            value = "\n".join((v.get("claim") if isinstance(v, dict) else str(v)) for v in value)
        values[m["target"]] = value if isinstance(value, str) else str(value)
    return values
