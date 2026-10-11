from app.blueprint import validate_blueprint
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


def test_build_blueprint_treats_missing_list_answer_as_empty():
    blueprint = build_blueprint({"agent_core.capabilities": None})
    assert blueprint["agent_core"]["capabilities"] == []


def test_empty_name_and_description_fail_validation():
    answers = {
        "agent_core.agent_type": "specialist",
        "agent_core.name": "",
        "agent_core.description": "   ",
        "agent_core.model_role": "research",
        "agent_core.capabilities": "research",
        "knowledge_boundary.allowed_sources": ["task.input"],
        "action_engine.actions": "research",
        "tools_plugins.tools": ["web_search"],
        "memory.long_term_sources": "",
        "external_integrations.services": "",
        "orchestration.approval_gate": "checkpoint",
        "security.permissions.network": False,
        "security.permissions.repo_write": False,
        "security.permissions.deploy": False,
        "security.budget.max_cost": "1",
        "scalability.evaluation": "clear answers",
    }
    blueprint = build_blueprint(answers)
    issues = validate_blueprint(blueprint)
    assert any(i.startswith("agent_core.name") for i in issues)
    assert any(i.startswith("agent_core.description") for i in issues)


# ---------- the interview in the owner's language (fa / en / tr) ----------

LOCALES = ("fa", "en", "tr")


def test_every_question_option_and_note_has_all_three_languages():
    for q in load_questions():
        for field in ("text", "note"):
            if field in q:
                assert set(q[field]) == set(LOCALES) and all(q[field][loc].strip() for loc in LOCALES), (q["id"], field)
        for o in q.get("options", []):
            assert set(o["label"]) == set(LOCALES) and all(o["label"][loc].strip() for loc in LOCALES), (q["id"], o["value"])


def test_question_is_shown_in_the_requested_language(client):
    fa = client.get("/interview", params={"locale": "fa"}).json()["question"]
    en = client.get("/interview", params={"locale": "en"}).json()["question"]
    tr = client.get("/interview", params={"locale": "tr"}).json()["question"]
    assert fa["target"] == en["target"] == tr["target"] == "agent_core.agent_type"
    assert en["text"].startswith("Is this agent") and tr["text"].startswith("Bu ajan")
    assert "Agent" in fa["text"] and fa["text"] != en["text"]
    assert [o["value"] for o in en["options"]] == [o["value"] for o in fa["options"]]
    assert all(isinstance(o["label"], str) for o in tr["options"])


def test_changing_language_mid_interview_keeps_the_answers_and_the_place(client):
    first = client.post("/interview/answer", json={"answers": {}, "target": "agent_core.agent_type",
                                                   "value": "specialist", "locale": "fa"}).json()
    answers = first["answers"]
    again = client.post("/interview/answer", json={"answers": answers, "target": "agent_core.name",
                                                   "value": "ticket_triage", "locale": "en"}).json()
    assert again["answers"] == {"agent_core.agent_type": "specialist", "agent_core.name": "ticket_triage"}
    assert again["question"]["target"] == "agent_core.description"
    assert again["question"]["text"].startswith("What exactly")
    import json as _json
    tr = client.get("/interview", params={"answers": _json.dumps(again["answers"]), "locale": "tr"}).json()
    assert tr["question"]["target"] == "agent_core.description" and tr["question"]["text"].startswith("Bu ajan")


def test_an_unknown_locale_falls_back_to_the_default(client):
    q = client.get("/interview", params={"locale": "de"}).json()["question"]
    assert q["text"] == client.get("/interview", params={"locale": "fa"}).json()["question"]["text"]


def test_a_text_missing_a_language_falls_back_to_english():
    from app.interview import localized

    assert localized({"fa": "x", "en": "y"}, "tr") == "y"
    assert localized({"fa": "x"}, "tr") == "x"
    assert localized("plain", "en") == "plain"


def test_malformed_interview_input_is_a_422_not_a_500(client):
    assert client.get("/interview", params={"answers": "[1, 2]"}).status_code == 422
    assert client.post("/interview/answer", json={"answers": [1], "target": "x", "value": 1}).status_code == 422
    assert client.post("/interview/answer", json={"answers": {}, "target": ["x"], "value": 1}).status_code == 422


def test_state_is_restored_by_post_so_answers_never_travel_in_a_url(client):
    long_answer = "توضیح " * 4000  # ~28K characters: far past any request-line limit as a GET
    answers = {"agent_core.agent_type": "specialist", "agent_core.name": "x", "agent_core.description": long_answer}
    r = client.post("/interview/state", json={"answers": answers, "locale": "en"})
    assert r.status_code == 200
    assert r.json()["question"]["target"] == "agent_core.model_role"
    assert r.json()["question"]["text"].startswith("Which model role")
    assert client.post("/interview/state", json={"answers": [1]}).status_code == 422
