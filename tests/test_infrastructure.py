import pytest

from app.context import ContextError, agent_directory, system_prompt, task_context
from app.gateway.providers import FakeProvider, ProviderError
from app.gateway.service import BudgetExceeded, Gateway, spent_usd
from app.models import Agent, ModelCall, Project, Task, TaskDependency, User
from app.registry import AgentSpec, RegistryError, upsert_agent
from app.settings_layers import resolve

# ---------- settings layers ----------


def test_settings_layers_override_in_order(client, project):
    pid = project["id"]
    ws = client.post("/workspaces", json={"name": "Acme"}).json()
    user_id = project["owner_id"]
    p2 = client.post("/projects", json={"owner_id": user_id, "workspace_id": ws["id"], "title": "B", "goal": "g"}).json()
    task = client.post(f"/projects/{p2['id']}/tasks", json={"title": "t"}).json()

    client.put("/settings/global/0", json={"max_steps": 4, "budget": {"task_usd": 0.5}})
    client.put(f"/settings/workspace/{ws['id']}", json={"mode": "automatic"})
    client.put(f"/settings/project/{p2['id']}", json={"budget": {"project_usd": 2.0}})
    client.put(f"/settings/task/{task['id']}", json={"max_steps": 2})

    r = client.get("/settings/resolved", params={"task_id": task["id"]}).json()
    assert r["max_steps"] == 2  # task beats global
    assert r["mode"] == "automatic"  # from workspace, found via the task's project
    assert r["budget"] == {"project_usd": 2.0, "task_usd": 0.5, "max_output_tokens": 2000}  # nested merge
    # The other project (no workspace) only sees global + defaults.
    other = client.get("/settings/resolved", params={"project_id": pid}).json()
    assert other["mode"] == "manual_learning" and other["max_steps"] == 4


def test_settings_reject_unknown_keys_and_scopes(client, project):
    assert client.put("/settings/global/0", json={"colour": "red"}).status_code == 422
    assert client.put("/settings/global/1", json={}).status_code == 422
    assert client.put("/settings/project/999", json={}).status_code == 404


def test_list_workspaces(client):
    assert client.get("/workspaces").json() == []
    client.post("/workspaces", json={"name": "Acme"})
    client.post("/workspaces", json={"name": "Beta"})
    names = [w["name"] for w in client.get("/workspaces").json()]
    assert names == ["Acme", "Beta"]


# ---------- registry ----------


def test_manager_is_registered_with_skills(client):
    manager = client.get("/agents/manager").json()
    assert manager["model_role"] == "manager"
    assert "planning" in manager["capabilities"]
    assert client.get("/skills/plan-goal").json()["body"]
    assert [a["name"] for a in client.get("/agents", params={"capability": "planning"}).json()] == ["manager"]


def test_registry_rejects_duplicates_and_unknowns(client):
    base = {"description": "d", "model_role": "research", "capabilities": ["web_research"], "tools": ["web_search"]}
    assert client.post("/agents", json={**base, "name": "researcher"}).status_code == 201
    dup = client.post("/agents", json={**base, "name": "researcher2"})
    assert dup.status_code == 422 and "duplicate" in dup.json()["detail"]
    assert client.post("/agents", json={**base, "name": "researcher"}).status_code == 422
    bad_tool = client.post("/agents", json={**base, "name": "x1", "capabilities": ["a"], "tools": ["rm_rf"]})
    assert bad_tool.status_code == 422
    bad_skill = client.post("/agents", json={**base, "name": "x2", "capabilities": ["b"], "skills": ["nope"]})
    assert bad_skill.status_code == 422


# ---------- gateway ----------


def make_project(session, **kw) -> Project:
    user = User(email=f"u{session.query(User).count()}@x.com")
    session.add(user)
    session.flush()
    project = Project(owner_id=user.id, title="p", goal="Validate an idea", **kw)
    session.add(project)
    session.commit()
    return project


def use_fake(session, **replies):
    fake = FakeProvider(replies=list(replies.get("replies", [])))
    from app.models import SettingsLayer, SettingsScope

    session.add(SettingsLayer(scope=SettingsScope.GLOBAL, scope_id=0, values={
        "models": {"manager": {"provider": "fake", "model": "claude-opus-5", "effort": "low"}},
    }))
    session.commit()
    return Gateway(session, providers={"fake": fake}), fake


def test_gateway_routes_by_role_and_logs_usage(session):
    project = make_project(session)
    gw, fake = use_fake(session, replies=[{"steps": []}])
    r = gw.call("manager", system="S" * 400, user="U" * 400, project_id=project.id, agent="manager",
                json_schema={"type": "object"})
    assert r.data == {"steps": []}
    assert fake.requests[0].model == "claude-opus-5" and fake.requests[0].effort == "low"
    call = session.query(ModelCall).one()
    assert call.agent == "manager" and call.provider == "fake" and call.input_tokens == 200
    assert call.cost_usd > 0
    assert spent_usd(session, project_id=project.id) == pytest.approx(call.cost_usd)


def test_budget_blocks_the_call_and_records_event(session, client):
    project = make_project(session, budget=0.000001)
    gw, fake = use_fake(session)
    with pytest.raises(BudgetExceeded):
        gw.call("manager", system="s", user="u", project_id=project.id)
    assert fake.requests == []  # nothing was sent
    events = client.get(f"/projects/{project.id}/events").json()
    assert events[-1]["type"] == "budget.exceeded"


def test_list_model_calls_for_a_project(session, client):
    project = make_project(session)
    gw, fake = use_fake(session, replies=[{"steps": []}])
    gw.call("manager", system="s", user="u", project_id=project.id, agent="manager", json_schema={"type": "object"})
    calls = client.get(f"/projects/{project.id}/model_calls").json()
    assert len(calls) == 1
    assert calls[0]["agent"] == "manager" and calls[0]["provider"] == "fake" and calls[0]["ok"] is True
    assert client.get("/projects/999/model_calls").status_code == 404


def test_failed_call_is_logged_and_unconfigured_provider_errors(session):
    project = make_project(session)
    gw = Gateway(session)  # real providers; openai adapter not configured
    from app.models import SettingsLayer, SettingsScope

    session.add(SettingsLayer(scope=SettingsScope.PROJECT, scope_id=project.id, values={
        "models": {"cheap": {"provider": "openai", "model": "gpt-x"}},
    }))
    session.commit()
    with pytest.raises(ProviderError):
        gw.call("cheap", system="s", user="u", project_id=project.id)
    call = session.query(ModelCall).one()
    assert call.ok is False and "not configured" in call.error


# ---------- context builder ----------


def test_system_prompt_loads_only_requested_granted_skills(session):
    manager = session.query(Agent).filter_by(name="manager").one()
    prompt = system_prompt(session, manager, ["plan-goal"])
    assert "Skill: plan-goal" in prompt and "Skill: delegate" not in prompt
    assert prompt == system_prompt(session, manager, ["plan-goal"])  # stable → cacheable
    with pytest.raises(ContextError):
        system_prompt(session, manager, ["not-granted"])
    assert "- manager: planning" in agent_directory(session)


def test_task_context_includes_only_dependencies_and_respects_cap(session):
    project = make_project(session)
    a = Task(project_id=project.id, title="Research", output={"summary": "Three pains found"})
    b = Task(project_id=project.id, title="Unrelated", output={"summary": "SHOULD NOT APPEAR"})
    session.add_all([a, b])
    session.flush()
    c = Task(project_id=project.id, title="Product", input={"x": 1})
    c.dependencies = [TaskDependency(depends_on_id=a.id)]
    session.add(c)
    session.commit()

    ctx = task_context(session, c)
    assert "Three pains found" in ctx and "SHOULD NOT APPEAR" not in ctx
    assert ctx.startswith("Project goal: Validate an idea")

    from app.models import SettingsLayer, SettingsScope

    session.add(SettingsLayer(scope=SettingsScope.TASK, scope_id=c.id, values={"context": {"max_chars": 120}}))
    session.commit()
    assert len(task_context(session, c)) <= 120


def test_registry_upsert_validates(session):
    with pytest.raises(RegistryError):
        upsert_agent(session, AgentSpec(name="dup-manager", description="d", model_role="manager",
                                        capabilities=["planning", "delegation", "approval_requests",
                                                      "conflict_resolution", "learning_trace"]))
    assert resolve(session)["models"]["manager"]["model"] == "claude-opus-5"


def test_anthropic_provider_builds_cached_request_and_maps_usage():
    from types import SimpleNamespace

    from app.gateway.providers import AnthropicProvider, ModelRequest

    sent = {}

    class Messages:
        def create(self, **kw):
            sent.update(kw)
            return SimpleNamespace(
                stop_reason="end_turn", model="claude-opus-5",
                content=[SimpleNamespace(type="text", text='{"ok": true}')],
                usage=SimpleNamespace(input_tokens=10, output_tokens=5,
                                      cache_read_input_tokens=90, cache_creation_input_tokens=None),
            )

    provider = AnthropicProvider()
    provider._client = SimpleNamespace(messages=Messages())
    r = provider.complete(ModelRequest(model="claude-opus-5", system="S", user="U", max_output_tokens=100,
                                       json_schema={"type": "object"}, effort="low"))
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert sent["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": {"type": "object"}}}
    assert r.data == {"ok": True} and r.usage.cache_read_tokens == 90 and r.usage.cache_write_tokens == 0
