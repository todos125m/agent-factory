from app.interview import build_blueprint, load_questions, next_question


def test_first_question_is_agent_type():
    q = next_question({})
    assert q["target"] == "agent_core.agent_type"
    assert q["type"] == "single_choice"


def test_persona_questions_skipped_for_specialist():
    answers = {"agent_core.agent_type": "specialist"}
    questions = load_questions()
    seen_targets = []
    while (q := next_question(answers, questions)) is not None:
        seen_targets.append(q["target"])
        answers[q["target"]] = q["options"][0]["value"] if q.get("options") else "x"
    assert not any(t.startswith("persona.") for t in seen_targets)
    assert "observation.record" not in seen_targets  # only asked for persona agents


def test_persona_questions_asked_for_persona_type():
    answers = {"agent_core.agent_type": "persona"}
    questions = load_questions()
    seen_targets = []
    while (q := next_question(answers, questions)) is not None:
        seen_targets.append(q["target"])
        if q.get("options"):
            answers[q["target"]] = q["options"][0]["value"]
        elif q["type"] == "multi_choice":
            answers[q["target"]] = [o["value"] for o in q["options"]] if q.get("options") else ["x"]
        elif q["type"] == "confirm":
            answers[q["target"]] = False
        else:
            answers[q["target"]] = "x"
    assert "persona.name" in seen_targets
    assert "observation.record" in seen_targets


def test_answered_question_is_not_asked_again():
    q = next_question({"agent_core.agent_type": "specialist"})
    assert q["target"] != "agent_core.agent_type"


def test_build_blueprint_fills_owner_fixed_defaults():
    answers = {
        "agent_core.agent_type": "specialist",
        "agent_core.name": "research",
        "agent_core.description": "desc",
        "agent_core.model_role": "research",
        "agent_core.capabilities": "research, analysis",
        "knowledge_boundary.allowed_sources": ["task.input"],
        "knowledge_boundary.general_llm_knowledge": True,  # owner decision must override this
        "action_engine.actions": "research",
        "tools_plugins.tools": ["web_search"],
        "memory.long_term_sources": "",
        "external_integrations.services": "",
        "orchestration.approval_gate": "checkpoint",
        "security.permissions.network": True,
        "security.permissions.repo_write": False,
        "security.permissions.deploy": False,
        "security.budget.max_cost": "2.5",
        "security.data_classification": "internal",
        "scalability.evaluation": "clear answers",
    }
    blueprint = build_blueprint(answers)
    assert blueprint["knowledge_boundary"]["general_llm_knowledge"] is False
    assert blueprint["data_storage"]["backend"] == "postgres_pgvector"
    assert blueprint["scalability"]["deployment"] == "docker_compose"
    assert blueprint["memory"]["short_term"] is True
    assert blueprint["orchestration"]["via_orchestrator"] is True
    assert blueprint["agent_core"]["capabilities"] == ["research", "analysis"]
    assert blueprint["security"]["budget"]["max_cost"] == 2.5
    assert blueprint["observation"]["record"] == ["runevent"]
    assert "persona" not in blueprint


def test_interview_get_endpoint(client):
    resp = client.get("/interview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["done"] is False
    assert body["question"]["target"] == "agent_core.agent_type"


def test_interview_answer_endpoint_advances(client):
    resp = client.post("/interview/answer", json={"answers": {}, "target": "agent_core.agent_type", "value": "specialist"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["answers"]["agent_core.agent_type"] == "specialist"
    assert body["question"]["target"] == "agent_core.name"


def test_interview_answer_endpoint_rejects_body_without_target(client):
    resp = client.post("/interview/answer", json={"answers": {}, "value": "specialist"})
    assert resp.status_code == 422


def test_build_blueprint_normalizes_persian_digits_in_budget():
    blueprint = build_blueprint({"security.budget.max_cost": "۲.۵"})
    assert blueprint["security"]["budget"]["max_cost"] == 2.5
