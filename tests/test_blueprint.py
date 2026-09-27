from app.blueprint import validate_blueprint

VALID_SPECIALIST = {
    "agent_core": {
        "name": "research",
        "description": "Gathers market info",
        "model_role": "research",
        "agent_type": "specialist",
        "capabilities": ["research"],
    },
    "knowledge_boundary": {"allowed_sources": ["task.input"], "general_llm_knowledge": False},
    "action_engine": {"actions": ["research"]},
    "tools_plugins": {"tools": ["web_search"]},
    "memory": {"short_term": True, "long_term_sources": []},
    "observation": {"record": ["runevent"]},
    "data_storage": {"backend": "postgres_pgvector"},
    "external_integrations": {"services": []},
    "orchestration": {"via_orchestrator": True, "approval_gate": "checkpoint"},
    "security": {
        "permissions": {"network": True, "repo_write": False, "deploy": False},
        "budget": {"max_cost": 1.5},
    },
    "scalability": {"deployment": "docker_compose", "evaluation": ["90% tasks without escalation"]},
}


def _with_persona(**overrides):
    data = {
        **VALID_SPECIALIST,
        "agent_core": {**VALID_SPECIALIST["agent_core"], "agent_type": "persona"},
        "persona": {
            "name": "first_time_freelancer",
            "knows": ["telegram"],
            "does_not_understand": ["api_keys"],
            "patience": "low",
            "goals": ["find_cheap_tool"],
        },
        "observation": {"record": ["cognitive", "emotional", "behavioral"]},
    }
    data.update(overrides)
    return data


def test_valid_specialist_blueprint_has_no_issues():
    assert validate_blueprint(VALID_SPECIALIST) == []


def test_valid_persona_blueprint_has_no_issues():
    assert validate_blueprint(_with_persona()) == []


def test_missing_sections_are_all_reported():
    issues = validate_blueprint({"agent_core": VALID_SPECIALIST["agent_core"]})
    for section in ("knowledge_boundary", "action_engine", "tools_plugins", "memory", "observation",
                     "data_storage", "external_integrations", "orchestration", "security", "scalability"):
        assert any(issue.startswith(f"{section}:") for issue in issues), issues


def test_persona_section_required_for_persona_type():
    data = _with_persona()
    del data["persona"]
    issues = validate_blueprint(data)
    assert any(i.startswith("persona.") for i in issues)


def test_persona_section_forbidden_for_specialist_type():
    data = {**VALID_SPECIALIST, "persona": {"name": "x", "knows": [], "does_not_understand": [], "patience": "low", "goals": ["g"]}}
    issues = validate_blueprint(data)
    assert any(i.startswith("persona:") for i in issues)


def test_unknown_tool_is_rejected():
    data = {**VALID_SPECIALIST, "tools_plugins": {"tools": ["nuke_everything"]}}
    issues = validate_blueprint(data)
    assert any("tools_plugins.tools" in i for i in issues)


def test_bad_permissions_shape_is_rejected():
    data = {**VALID_SPECIALIST, "security": {"permissions": {"network": True}, "budget": {"max_cost": 1}}}
    issues = validate_blueprint(data)
    assert any(i.startswith("security.permissions") for i in issues)


def test_general_llm_knowledge_must_be_false():
    data = {**VALID_SPECIALIST, "knowledge_boundary": {"allowed_sources": ["task.input"], "general_llm_knowledge": True}}
    issues = validate_blueprint(data)
    assert any(i.startswith("knowledge_boundary.general_llm_knowledge") for i in issues)


def test_specialist_observation_limited_to_runevent():
    data = {**VALID_SPECIALIST, "observation": {"record": ["cognitive"]}}
    issues = validate_blueprint(data)
    assert any(i.startswith("observation.record") for i in issues)


def test_owner_fixed_stack_choices_enforced():
    data = {**VALID_SPECIALIST, "data_storage": {"backend": "mongodb"}, "scalability": {**VALID_SPECIALIST["scalability"], "deployment": "kubernetes"}}
    issues = validate_blueprint(data)
    assert any("postgres_pgvector" in i for i in issues)
    assert any("docker_compose" in i for i in issues)


def test_missing_budget_is_rejected():
    data = {**VALID_SPECIALIST, "security": {"permissions": VALID_SPECIALIST["security"]["permissions"], "budget": {}}}
    issues = validate_blueprint(data)
    assert any(i.startswith("security.budget") for i in issues)
