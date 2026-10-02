"""Guided interview that fills out an Agent Blueprint (docs/AGENT_BLUEPRINT.md).

Deterministic: which question comes next, and how answers become a blueprint, are pure code.
A model is only ever used off to the side, to turn one free-text answer into its target field
(`registry/skills/agent-intake.md`) — never to pick the next question or to validate anything.

Question files: `registry/interview/*.yaml`, loaded in filename order (most decisive sections first).
Each file has `section`, an optional section-level `depends_on`, and `questions` (each with `id`,
`text`, `type`, `target`, optional `options` / question-level `depends_on` / `note`).
"""

from pathlib import Path
from typing import Any

import yaml

INTERVIEW_DIR = Path(__file__).resolve().parent.parent / "registry" / "interview"

# Fields the interview collects as free text but the blueprint schema expects as arrays.
LIST_FIELDS = {
    "agent_core.capabilities",
    "persona.knows",
    "persona.does_not_understand",
    "persona.goals",
    "action_engine.actions",
    "external_integrations.services",
    "scalability.evaluation",
    "memory.long_term_sources",
}

# Owner decisions that are never asked as questions — always written into the blueprint as-is.
FIXED_DEFAULTS = {
    "knowledge_boundary.general_llm_knowledge": False,
    "memory.short_term": True,
    "data_storage.backend": "postgres_pgvector",
    "orchestration.via_orchestrator": True,
    "scalability.deployment": "docker_compose",
}


class InterviewError(ValueError):
    pass


def load_questions(root: Path = INTERVIEW_DIR) -> list[dict[str, Any]]:
    """Flat, ordered list of questions; each carries its section and section-level `depends_on`."""
    questions: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        section_condition = doc.get("depends_on", {})
        for q in doc["questions"]:
            merged = {**q, "section": doc["section"]}
            if section_condition and "depends_on" not in merged:
                merged["depends_on"] = section_condition
            questions.append(merged)
    return questions


def _condition_met(condition: dict[str, list[Any]] | None, answers: dict[str, Any]) -> bool:
    if not condition:
        return True
    return all(answers.get(key) in allowed for key, allowed in condition.items())


def next_question(answers: dict[str, Any], questions: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """The next unanswered question whose condition holds, or None when the interview is complete."""
    for q in questions if questions is not None else load_questions():
        if q["target"] in answers:
            continue
        if not _condition_met(q.get("depends_on"), answers):
            continue
        visible = dict(q)
        if "options" in visible:
            visible["options"] = [o for o in visible["options"] if _condition_met(o.get("depends_on"), answers)]
        return visible
    return None


_DIGIT_MAP = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def _normalize_digits(value: Any) -> Any:
    return str(value).translate(_DIGIT_MAP) if isinstance(value, str) else value


def _split_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in str(value).replace("،", ",").split(",") if part.strip()]


def _set_path(root: dict[str, Any], dotted: str, value: Any) -> None:
    *parents, leaf = dotted.split(".")
    node = root
    for key in parents:
        node = node.setdefault(key, {})
    node[leaf] = value


def build_blueprint(answers: dict[str, Any]) -> dict[str, Any]:
    """Turn the flat `target -> value` answers dict into a nested blueprint (no validation here)."""
    blueprint: dict[str, Any] = {}
    for target, value in answers.items():
        _set_path(blueprint, target, _split_list(value) if target in LIST_FIELDS else value)
    for target, value in FIXED_DEFAULTS.items():
        _set_path(blueprint, target, value)

    if blueprint.get("agent_core", {}).get("agent_type") == "specialist":
        blueprint.pop("persona", None)
        blueprint.setdefault("observation", {})["record"] = ["runevent"]

    budget = blueprint.get("security", {}).get("budget", {})
    if "max_cost" in budget:
        try:
            budget["max_cost"] = float(_normalize_digits(budget["max_cost"]))
        except (TypeError, ValueError):
            pass

    return blueprint
