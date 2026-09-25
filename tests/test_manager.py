from app.gateway.providers import FakeProvider
from app.gateway.service import Gateway
from app.main import app
from app.routers.manager import get_gateway


def use_fake(client, session_factory, replies):
    """Route the "manager" role to a FakeProvider queued with `replies`, via HTTP dependency override."""
    fake = FakeProvider(replies=list(replies))

    def override():
        with session_factory() as s:
            yield Gateway(s, providers={"fake": fake})

    app.dependency_overrides[get_gateway] = override
    client.put("/settings/global/0", json={"models": {"manager": {"provider": "fake", "model": "claude-opus-5"}}})
    return fake


GOOD_PLAN = {
    "understanding": "Validate a SaaS idea for small firms",
    "assumptions": ["Target market is SMEs"],
    "steps": [
        {"title": "Research", "agent": "researcher", "goal": "Find market pains", "depends_on": [], "risk": "low"},
        {"title": "Customer", "agent": "researcher", "goal": "Talk to 5 customers", "depends_on": [0], "risk": "medium"},
    ],
}


def register_researcher(client):
    client.post("/agents", json={
        "name": "researcher", "description": "d", "model_role": "research", "capabilities": ["web_research"],
    })


def test_plan_creates_the_dag(client, session_factory, project):
    register_researcher(client)
    pid = project["id"]
    use_fake(client, session_factory, [GOOD_PLAN])

    r = client.post(f"/projects/{pid}/plan")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["understanding"] == GOOD_PLAN["understanding"]
    assert len(body["task_ids"]) == 2
    assert body["specialists_requested"] == []

    tasks = client.get(f"/projects/{pid}/tasks").json()
    assert {t["title"] for t in tasks} == {"Research", "Customer"}
    customer = next(t for t in tasks if t["title"] == "Customer")
    research = next(t for t in tasks if t["title"] == "Research")
    assert customer["depends_on"] == [research["id"]]
    assert customer["risk"] == "medium" and research["status"] == "CREATED"

    events = [e["type"] for e in client.get(f"/projects/{pid}/events").json()]
    assert "plan.proposed" in events and "approval.requested" in events

    app.dependency_overrides.pop(get_gateway, None)


def test_unknown_agent_becomes_specialist_request(client, session_factory, project):
    pid = project["id"]
    plan = {
        "understanding": "u", "assumptions": [],
        "steps": [{"title": "Design", "agent": "designer", "goal": "make a logo", "depends_on": [], "risk": "low"}],
    }
    use_fake(client, session_factory, [plan])

    r = client.post(f"/projects/{pid}/plan")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["task_ids"] == []
    assert body["specialists_requested"] == ["designer"]
    assert client.get(f"/projects/{pid}/tasks").json() == []

    events = client.get(f"/projects/{pid}/events").json()
    assert any(e["type"] == "specialist.requested" and e["payload"]["agent"] == "designer" for e in events)

    app.dependency_overrides.pop(get_gateway, None)


def test_step_delegated_to_manager_becomes_specialist_request(client, session_factory, project):
    pid = project["id"]
    plan = {
        "understanding": "u", "assumptions": [],
        "steps": [{"title": "X", "agent": "manager", "goal": "g", "depends_on": [], "risk": "low"}],
    }
    use_fake(client, session_factory, [plan])
    body = client.post(f"/projects/{pid}/plan").json()
    assert body["specialists_requested"] == ["manager"]
    app.dependency_overrides.pop(get_gateway, None)


def test_invalid_dependency_index_rejected(client, session_factory, project):
    pid = project["id"]
    plan = {
        "understanding": "u", "assumptions": [],
        "steps": [{"title": "X", "agent": "researcher", "goal": "g", "depends_on": [5], "risk": "low"}],
    }
    register_researcher(client)
    use_fake(client, session_factory, [plan])
    r = client.post(f"/projects/{pid}/plan")
    assert r.status_code == 422
    assert client.get(f"/projects/{pid}/tasks").json() == []
    app.dependency_overrides.pop(get_gateway, None)


def test_cyclic_dependency_rejected(client, session_factory, project):
    pid = project["id"]
    register_researcher(client)
    plan = {
        "understanding": "u", "assumptions": [],
        "steps": [
            {"title": "A", "agent": "researcher", "goal": "g", "depends_on": [1], "risk": "low"},
            {"title": "B", "agent": "researcher", "goal": "g", "depends_on": [0], "risk": "low"},
        ],
    }
    use_fake(client, session_factory, [plan])
    r = client.post(f"/projects/{pid}/plan")
    assert r.status_code == 422 and "cycle" in r.json()["detail"]
    assert client.get(f"/projects/{pid}/tasks").json() == []
    app.dependency_overrides.pop(get_gateway, None)


def test_too_many_steps_rejected(client, session_factory, project):
    pid = project["id"]
    register_researcher(client)
    steps = [{"title": f"S{i}", "agent": "researcher", "goal": "g", "depends_on": [], "risk": "low"} for i in range(10)]
    plan = {"understanding": "u", "assumptions": [], "steps": steps}
    use_fake(client, session_factory, [plan])
    r = client.post(f"/projects/{pid}/plan")
    assert r.status_code == 422
    app.dependency_overrides.pop(get_gateway, None)


def test_approve_and_reject_replan(client, session_factory, project):
    pid = project["id"]
    register_researcher(client)
    use_fake(client, session_factory, [GOOD_PLAN])
    client.post(f"/projects/{pid}/plan")

    r = client.post(f"/projects/{pid}/plan/approve")
    assert r.status_code == 200
    tasks = {t["title"]: t for t in client.get(f"/projects/{pid}/tasks").json()}
    assert tasks["Research"]["status"] == "READY"  # no deps
    assert tasks["Customer"]["status"] == "CREATED"  # blocked on Research
    events = [e["type"] for e in client.get(f"/projects/{pid}/events").json()]
    assert "decision.plan_approved" in events


def test_reject_deletes_proposed_tasks_and_replans(client, session_factory, project):
    pid = project["id"]
    register_researcher(client)
    revised_plan = {**GOOD_PLAN, "understanding": "Revised understanding"}
    fake = use_fake(client, session_factory, [GOOD_PLAN, revised_plan])
    client.post(f"/projects/{pid}/plan")
    old_tasks = {t["id"] for t in client.get(f"/projects/{pid}/tasks").json()}

    r = client.post(f"/projects/{pid}/plan/reject", json={"feedback": "too shallow"})
    assert r.status_code == 200
    assert r.json()["understanding"] == "Revised understanding"

    new_tasks = client.get(f"/projects/{pid}/tasks").json()
    assert len(new_tasks) == 2
    assert len(fake.requests) == 2
    assert "too shallow" in fake.requests[1].user

    events = client.get(f"/projects/{pid}/events").json()
    rejected = next(e for e in events if e["type"] == "decision.plan_rejected")
    assert set(rejected["payload"]["deleted_task_ids"]) == old_tasks
    app.dependency_overrides.pop(get_gateway, None)


CHECKPOINT = {
    "challenge": "Which market to target first?",
    "options": [{"title": "SMEs", "tradeoff": "smaller but faster sales"}, {"title": "Enterprise", "tradeoff": "bigger but slower"}],
    "recommended": 0,
    "why": "Faster feedback loop",
}


def _ready_task(client, pid, **kw):
    t = client.post(f"/projects/{pid}/tasks", json={"title": "T", **kw}).json()
    client.post(f"/projects/{pid}/tasks/{t['id']}/transition", json={"status": "READY"})
    return t


def test_manual_checkpoint_waits_for_decision(client, session_factory, project):
    pid = project["id"]
    t = _ready_task(client, pid, risk="low")
    use_fake(client, session_factory, [CHECKPOINT])

    r = client.post(f"/projects/{pid}/tasks/{t['id']}/checkpoint")
    assert r.status_code == 200, r.text
    assert r.json()["auto_decided"] is False
    assert client.get(f"/projects/{pid}/tasks/{t['id']}").json()["status"] == "READY"

    d = client.post(f"/projects/{pid}/tasks/{t['id']}/decide", json={"option": 0, "note": "go with SMEs"})
    assert d.status_code == 200
    assert d.json()["status"] == "RUNNING"
    events = [e["type"] for e in client.get(f"/projects/{pid}/events").json()]
    assert "checkpoint.created" in events and "decision.step" in events
    app.dependency_overrides.pop(get_gateway, None)


def test_automatic_mode_auto_decides_low_risk_but_waits_on_high_risk(client, session_factory, project):
    pid = project["id"]
    client.put(f"/settings/project/{pid}", json={"mode": "automatic"})

    low = _ready_task(client, pid, risk="low")
    use_fake(client, session_factory, [CHECKPOINT])
    r = client.post(f"/projects/{pid}/tasks/{low['id']}/checkpoint")
    assert r.json()["auto_decided"] is True
    assert client.get(f"/projects/{pid}/tasks/{low['id']}").json()["status"] == "RUNNING"

    high = _ready_task(client, pid, risk="high")
    use_fake(client, session_factory, [CHECKPOINT])
    r = client.post(f"/projects/{pid}/tasks/{high['id']}/checkpoint")
    assert r.json()["auto_decided"] is False
    assert client.get(f"/projects/{pid}/tasks/{high['id']}").json()["status"] == "READY"
    app.dependency_overrides.pop(get_gateway, None)


def test_budget_exceeded_returns_402(client, session_factory):
    user = client.post("/users", json={"email": "b@example.com"}).json()
    project = client.post(
        "/projects", json={"owner_id": user["id"], "title": "P", "goal": "g", "budget": 0.000001}
    ).json()
    use_fake(client, session_factory, [GOOD_PLAN])

    r = client.post(f"/projects/{project['id']}/plan")
    assert r.status_code == 402
    events = [e["type"] for e in client.get(f"/projects/{project['id']}/events").json()]
    assert "budget.exceeded" in events
    app.dependency_overrides.pop(get_gateway, None)


def test_plan_system_prompt_has_only_requested_skills(client, session_factory, project):
    pid = project["id"]
    register_researcher(client)
    fake = use_fake(client, session_factory, [GOOD_PLAN])
    client.post(f"/projects/{pid}/plan")
    prompt = fake.requests[0].system
    assert "Skill: plan-goal" in prompt and "Skill: delegate" in prompt and "Skill: evidence" in prompt
    assert "Skill: decision-checkpoint" not in prompt and "Skill: resolve-conflict" not in prompt
    app.dependency_overrides.pop(get_gateway, None)


def test_checkpoint_system_prompt_has_only_requested_skills(client, session_factory, project):
    pid = project["id"]
    t = _ready_task(client, pid, risk="low")
    fake = use_fake(client, session_factory, [CHECKPOINT])
    client.post(f"/projects/{pid}/tasks/{t['id']}/checkpoint")
    prompt = fake.requests[0].system
    assert "Skill: decision-checkpoint" in prompt and "Skill: evidence" in prompt
    assert "Skill: plan-goal" not in prompt and "Skill: delegate" not in prompt
    app.dependency_overrides.pop(get_gateway, None)
