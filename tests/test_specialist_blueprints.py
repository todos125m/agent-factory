"""Phase 4 first specialists (docs/AGENT_BLUEPRINT.md): each registry/agents/*.json file must be
the AgentSpec projection of a Blueprint that satisfies the mandatory 14-section standard.
"""

import json

import pytest

from app.blueprint import validate_blueprint
from app.registry import REGISTRY_DIR

SPECIALIST_BLUEPRINTS = {
    "researcher": {
        "agent_core": {
            "name": "researcher", "description": "Gathers and synthesizes market/domain research strictly from the task context.",
            "model_role": "research", "agent_type": "specialist",
            "capabilities": ["market_research", "research_synthesis"],
        },
        "knowledge_boundary": {"allowed_sources": ["task.input", "memory.phase_current", "skills"], "general_llm_knowledge": False},
        "action_engine": {"actions": ["market_research", "research_synthesis"]},
        "tools_plugins": {"tools": ["web_search"]},
        "memory": {"short_term": True, "long_term_sources": []},
        "observation": {"record": ["runevent"]},
        "data_storage": {"backend": "postgres_pgvector"},
        "external_integrations": {"services": []},
        "orchestration": {"via_orchestrator": True, "approval_gate": "checkpoint"},
        "security": {
            "permissions": {"network": True, "repo_write": False, "deploy": False},
            "budget": {"max_cost": 1.0},
        },
        "scalability": {"deployment": "docker_compose", "evaluation": ["90% of tasks completed without escalation"]},
    },
    "customer": {
        "agent_core": {
            "name": "customer", "description": "Reasons about customer needs, pains and behavior strictly from the task context.",
            "model_role": "research", "agent_type": "specialist",
            "capabilities": ["customer_discovery", "customer_interviews"],
        },
        "knowledge_boundary": {"allowed_sources": ["task.input", "memory.phase_current", "skills"], "general_llm_knowledge": False},
        "action_engine": {"actions": ["customer_discovery", "customer_interviews"]},
        "tools_plugins": {"tools": ["web_search"]},
        "memory": {"short_term": True, "long_term_sources": []},
        "observation": {"record": ["runevent"]},
        "data_storage": {"backend": "postgres_pgvector"},
        "external_integrations": {"services": []},
        "orchestration": {"via_orchestrator": True, "approval_gate": "checkpoint"},
        "security": {
            "permissions": {"network": True, "repo_write": False, "deploy": False},
            "budget": {"max_cost": 1.0},
        },
        "scalability": {"deployment": "docker_compose", "evaluation": ["90% of tasks completed without escalation"]},
    },
    "strategy": {
        "agent_core": {
            "name": "strategy", "description": "Frames strategic options and trade-offs from findings already gathered in context.",
            "model_role": "research", "agent_type": "specialist",
            "capabilities": ["strategy_synthesis", "positioning"],
        },
        "knowledge_boundary": {"allowed_sources": ["task.input", "memory.phase_current", "skills"], "general_llm_knowledge": False},
        "action_engine": {"actions": ["strategy_synthesis", "positioning"]},
        "tools_plugins": {"tools": ["web_search"]},
        "memory": {"short_term": True, "long_term_sources": []},
        "observation": {"record": ["runevent"]},
        "data_storage": {"backend": "postgres_pgvector"},
        "external_integrations": {"services": []},
        "orchestration": {"via_orchestrator": True, "approval_gate": "checkpoint"},
        "security": {
            "permissions": {"network": True, "repo_write": False, "deploy": False},
            "budget": {"max_cost": 1.0},
        },
        "scalability": {"deployment": "docker_compose", "evaluation": ["90% of tasks completed without escalation"]},
    },
    "product": {
        "agent_core": {
            "name": "product", "description": "Turns validated findings into a scoped product definition.",
            "model_role": "research", "agent_type": "specialist",
            "capabilities": ["product_definition", "prioritization"],
        },
        "knowledge_boundary": {"allowed_sources": ["task.input", "memory.phase_current", "skills"], "general_llm_knowledge": False},
        "action_engine": {"actions": ["product_definition", "prioritization"]},
        "tools_plugins": {"tools": []},
        "memory": {"short_term": True, "long_term_sources": []},
        "observation": {"record": ["runevent"]},
        "data_storage": {"backend": "postgres_pgvector"},
        "external_integrations": {"services": []},
        "orchestration": {"via_orchestrator": True, "approval_gate": "checkpoint"},
        "security": {
            "permissions": {"network": False, "repo_write": False, "deploy": False},
            "budget": {"max_cost": 1.0},
        },
        "scalability": {"deployment": "docker_compose", "evaluation": ["90% of tasks completed without escalation"]},
    },
}


@pytest.mark.parametrize("name", sorted(SPECIALIST_BLUEPRINTS))
def test_specialist_blueprint_is_valid(name):
    assert validate_blueprint(SPECIALIST_BLUEPRINTS[name]) == []


@pytest.mark.parametrize("name", sorted(SPECIALIST_BLUEPRINTS))
def test_registered_agent_matches_its_blueprint(name):
    """The registry/agents/*.json file actually registered must agree with the validated Blueprint
    above on every field Blueprint §1/§5/§13 map onto an AgentSpec (docs/AGENT_BLUEPRINT.md)."""
    spec = json.loads((REGISTRY_DIR / "agents" / f"{name}.json").read_text(encoding="utf-8"))
    bp = SPECIALIST_BLUEPRINTS[name]
    assert spec["name"] == bp["agent_core"]["name"]
    assert spec["model_role"] == bp["agent_core"]["model_role"]
    assert set(spec["capabilities"]) == set(bp["agent_core"]["capabilities"])
    assert set(spec["tools"]) == set(bp["tools_plugins"]["tools"])
    assert spec["permissions"] == bp["security"]["permissions"]
    assert "evidence" in spec["skills"]  # Synthesis engine (suspend conclusions until evidence supports them)


def test_specialists_have_distinct_capability_sets():
    caps = [frozenset(bp["agent_core"]["capabilities"]) for bp in SPECIALIST_BLUEPRINTS.values()]
    assert len(caps) == len(set(caps))


def test_specialists_are_registered_and_active(client):
    for name in SPECIALIST_BLUEPRINTS:
        agent = client.get(f"/agents/{name}").json()
        assert agent["active"] is True
        assert agent["model_role"] == "research"
