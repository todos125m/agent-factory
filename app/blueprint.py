"""Agent Blueprint validator (docs/AGENT_BLUEPRINT.md).

Deterministic, no model calls — mirrors `registry/blueprint.schema.json`. Returns a flat list of
`"section.field: reason"` strings; an empty list means the blueprint may become an `AgentSpec`
(app/registry.py).
"""

from typing import Any

from app.registry import KNOWN_TOOLS, MODEL_ROLES

REQUIRED_SECTIONS = [
    "agent_core",
    "knowledge_boundary",
    "action_engine",
    "tools_plugins",
    "memory",
    "observation",
    "data_storage",
    "external_integrations",
    "orchestration",
    "security",
    "scalability",
]

AGENT_TYPES = {"persona", "specialist"}
PATIENCE_LEVELS = {"low", "medium", "high"}
ALLOWED_SOURCES = {"persona.knows", "memory.phase_current", "task.input", "skills"}
OBSERVATION_TYPES = {"cognitive", "emotional", "behavioral", "runevent"}
APPROVAL_GATES = {"auto", "checkpoint", "always"}
DATA_CLASSIFICATIONS = {"public", "internal", "sensitive"}


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name)
    return value if isinstance(value, dict) else {}


def validate_blueprint(data: dict[str, Any]) -> list[str]:
    issues: list[str] = []

    for name in REQUIRED_SECTIONS:
        if not isinstance(data.get(name), dict):
            issues.append(f"{name}: missing required section")
    if issues:
        # Nothing else can be checked meaningfully once a whole section is absent.
        return issues

    core = _section(data, "agent_core")
    for field in ("name", "description", "model_role", "agent_type", "capabilities"):
        if field not in core:
            issues.append(f"agent_core.{field}: required")
    agent_type = core.get("agent_type")
    if agent_type is not None and agent_type not in AGENT_TYPES:
        issues.append(f"agent_core.agent_type: must be one of {sorted(AGENT_TYPES)}")
    if "model_role" in core and core["model_role"] not in MODEL_ROLES:
        issues.append(f"agent_core.model_role: must be one of {sorted(MODEL_ROLES)}")
    if "capabilities" in core and not core["capabilities"]:
        issues.append("agent_core.capabilities: must have at least one item")

    persona = _section(data, "persona")
    if agent_type == "persona":
        for field in ("name", "knows", "does_not_understand", "patience", "goals"):
            if field not in persona:
                issues.append(f"persona.{field}: required for agent_type=persona")
        if "patience" in persona and persona["patience"] not in PATIENCE_LEVELS:
            issues.append(f"persona.patience: must be one of {sorted(PATIENCE_LEVELS)}")
        if "goals" in persona and not persona["goals"]:
            issues.append("persona.goals: must have at least one item")
    elif agent_type == "specialist" and persona:
        issues.append("persona: must be absent/empty for agent_type=specialist (§ Persona Engine scope)")

    kb = _section(data, "knowledge_boundary")
    sources = kb.get("allowed_sources")
    if not sources:
        issues.append("knowledge_boundary.allowed_sources: required, at least one item")
    elif unknown := set(sources) - ALLOWED_SOURCES:
        issues.append(f"knowledge_boundary.allowed_sources: unknown sources {sorted(unknown)}")
    if kb.get("general_llm_knowledge") is not False:
        issues.append("knowledge_boundary.general_llm_knowledge: must be false")

    action = _section(data, "action_engine")
    if not action.get("actions"):
        issues.append("action_engine.actions: required, at least one item")

    tools = _section(data, "tools_plugins")
    tool_list = tools.get("tools")
    if tool_list is None:
        issues.append("tools_plugins.tools: required (may be empty list)")
    elif unknown := set(tool_list) - KNOWN_TOOLS:
        issues.append(f"tools_plugins.tools: unknown tools {sorted(unknown)}")

    memory = _section(data, "memory")
    if memory.get("short_term") is not True:
        issues.append("memory.short_term: must be true")
    if memory.get("long_term_sources") is None:
        issues.append("memory.long_term_sources: required (may be empty list)")

    observation = _section(data, "observation")
    record = observation.get("record")
    if not record:
        issues.append("observation.record: required, at least one item")
    elif unknown := set(record) - OBSERVATION_TYPES:
        issues.append(f"observation.record: unknown values {sorted(unknown)}")
    elif agent_type == "specialist" and record != ["runevent"]:
        issues.append('observation.record: specialist agents only get ["runevent"] (Synthesis + Memory scope)')

    storage = _section(data, "data_storage")
    if storage.get("backend") != "postgres_pgvector":
        issues.append('data_storage.backend: must be "postgres_pgvector" (owner decision — no MongoDB/Pinecone in V1)')

    integrations = _section(data, "external_integrations")
    if integrations.get("services") is None:
        issues.append("external_integrations.services: required (may be empty list)")

    orchestration = _section(data, "orchestration")
    if orchestration.get("via_orchestrator") is not True:
        issues.append("orchestration.via_orchestrator: must be true (§53 — no direct agent-to-agent calls)")
    if orchestration.get("approval_gate") not in APPROVAL_GATES:
        issues.append(f"orchestration.approval_gate: must be one of {sorted(APPROVAL_GATES)}")

    security = _section(data, "security")
    permissions = security.get("permissions")
    if not isinstance(permissions, dict) or set(permissions) != {"network", "repo_write", "deploy"}:
        issues.append("security.permissions: required, exactly {network, repo_write, deploy} booleans")
    elif not all(isinstance(v, bool) for v in permissions.values()):
        issues.append("security.permissions: values must be booleans")
    budget = security.get("budget")
    if not isinstance(budget, dict) or not ({"max_cost", "max_tokens"} & set(budget)):
        issues.append("security.budget: required, at least one of max_cost/max_tokens")
    else:
        for key in ("max_cost", "max_tokens"):
            if key in budget and not (isinstance(budget[key], (int, float)) and budget[key] > 0):
                issues.append(f"security.budget.{key}: must be a positive number")
    classification = security.get("data_classification", "internal")
    if classification not in DATA_CLASSIFICATIONS:
        issues.append(f"security.data_classification: must be one of {sorted(DATA_CLASSIFICATIONS)}")

    scalability = _section(data, "scalability")
    if scalability.get("deployment") != "docker_compose":
        issues.append('scalability.deployment: must be "docker_compose" (owner decision — no Kubernetes in V1)')
    if not scalability.get("evaluation"):
        issues.append("scalability.evaluation: required, at least one measurable criterion")

    return issues
