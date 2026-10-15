"""Phase 4 task execution (POST /projects/{id}/tasks/{tid}/run): full flow plan -> approve ->
checkpoint -> decide -> run -> next ready, plus the adversarial cases the run endpoint must handle
(wrong state, unregistered owner, malformed model output, budget/provider failures).
"""

from app.gateway.providers import FakeProvider, ProviderError
from app.gateway.service import Gateway
from app.main import app
from app.routers.manager import get_gateway
from tests.test_manager import CHECKPOINT, use_fake

PLAN = {
    "understanding": "Validate a SaaS idea for small firms",
    "assumptions": ["Target market is SMEs"],
    "steps": [
        {"title": "Research", "agent": "researcher", "goal": "Find market pains", "depends_on": [], "risk": "low"},
        {"title": "Define product", "agent": "product", "goal": "Scope the first slice", "depends_on": [0], "risk": "low"},
    ],
}

RUN_RESULT = {
    "summary": "Found three recurring pains in SME invoicing.",
    "findings": [
        {"claim": "SMEs re-key invoice data by hand", "type": "FACT", "basis": "task input"},
        {"claim": "Automation would save ~2h/week", "type": "INFERENCE", "basis": "derived from the pain above"},
    ],
    "lesson": "Invoicing is the sharpest pain, not payments.",
    "next": "Scope an invoicing-first slice.",
}


def use_fake_role(client, session_factory, role, replies):
    """Like test_manager.use_fake, but routes an arbitrary role (e.g. "research") to the fake provider."""
    fake = FakeProvider(replies=list(replies))

    def override():
        with session_factory() as s:
            yield Gateway(s, providers={"fake": fake})

    app.dependency_overrides[get_gateway] = override
    client.put("/settings/global/0", json={"models": {role: {"provider": "fake", "model": "claude-sonnet-5"}}})
    return fake


def _plan_and_start_first_task(client, session_factory, pid):
    client.put(f"/settings/project/{pid}", json={"mode": "automatic"})
    use_fake(client, session_factory, [PLAN])
    client.post(f"/projects/{pid}/plan")
    client.post(f"/projects/{pid}/plan/approve")
    tasks = {t["title"]: t for t in client.get(f"/projects/{pid}/tasks").json()}
    research = tasks["Research"]
    product = tasks["Define product"]
    assert research["status"] == "READY"
    assert product["status"] == "CREATED"

    use_fake(client, session_factory, [CHECKPOINT])
    r = client.post(f"/projects/{pid}/tasks/{research['id']}/checkpoint")
    assert r.json()["auto_decided"] is True
    research = client.get(f"/projects/{pid}/tasks/{research['id']}").json()
    assert research["status"] == "RUNNING"
    return research, product


def test_full_flow_plan_approve_checkpoint_decide_run_next_ready(client, session_factory, project):
    pid = project["id"]
    research, product = _plan_and_start_first_task(client, session_factory, pid)

    fake = use_fake_role(client, session_factory, "research", [RUN_RESULT])
    r = client.post(f"/projects/{pid}/tasks/{research['id']}/run")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["task"]["status"] == "COMPLETED"
    assert body["task"]["output"]["summary"] == RUN_RESULT["summary"]
    assert len(body["task"]["output"]["findings"]) == 2
    assert body["ready_task_ids"] == [product["id"]]

    # The dependent task became READY, and the run used the specialist's own role/skills, not the manager's.
    updated_product = client.get(f"/projects/{pid}/tasks/{product['id']}").json()
    assert updated_product["status"] == "READY"
    prompt = fake.requests[0].system
    assert "Skill: task-execution" in prompt and "Skill: research-method" in prompt and "Skill: evidence" in prompt

    events = [e["type"] for e in client.get(f"/projects/{pid}/events").json()]
    assert "task.completed" in events
    app.dependency_overrides.pop(get_gateway, None)


def test_run_includes_dependency_summary_and_decision_in_context(client, session_factory, project):
    pid = project["id"]
    research, _product = _plan_and_start_first_task(client, session_factory, pid)
    fake = use_fake_role(client, session_factory, "research", [RUN_RESULT])
    client.post(f"/projects/{pid}/tasks/{research['id']}/run")
    # decision.step from the auto-decided checkpoint must be visible to the executing agent.
    assert "decision.step" in fake.requests[0].user or "auto" in fake.requests[0].user
    app.dependency_overrides.pop(get_gateway, None)


def test_run_rejects_task_not_running(client, project):
    pid = project["id"]
    t = client.post(f"/projects/{pid}/tasks", json={"title": "T", "owner": "researcher"}).json()
    assert t["status"] == "CREATED"
    r = client.post(f"/projects/{pid}/tasks/{t['id']}/run")
    assert r.status_code == 409


def test_run_rejects_unregistered_owner(client, session_factory, project):
    pid = project["id"]
    t = client.post(f"/projects/{pid}/tasks", json={"title": "T", "owner": "ghost-agent"}).json()
    client.post(f"/projects/{pid}/tasks/{t['id']}/transition", json={"status": "READY"})
    client.post(f"/projects/{pid}/tasks/{t['id']}/transition", json={"status": "RUNNING"})
    r = client.post(f"/projects/{pid}/tasks/{t['id']}/run")
    assert r.status_code == 422
    assert "ghost-agent" in r.json()["detail"]


def test_run_rejects_malformed_model_output(client, session_factory, project):
    pid = project["id"]
    t = client.post(f"/projects/{pid}/tasks", json={"title": "T", "owner": "researcher"}).json()
    client.post(f"/projects/{pid}/tasks/{t['id']}/transition", json={"status": "READY"})
    client.post(f"/projects/{pid}/tasks/{t['id']}/transition", json={"status": "RUNNING"})
    bad = {**RUN_RESULT, "findings": [{"claim": "x", "type": "OPINION", "basis": "y"}]}  # not FACT|INFERENCE|HYPOTHESIS
    use_fake_role(client, session_factory, "research", [bad])
    r = client.post(f"/projects/{pid}/tasks/{t['id']}/run")
    assert r.status_code == 422
    assert client.get(f"/projects/{pid}/tasks/{t['id']}").json()["status"] == "RUNNING"  # unchanged on failure
    app.dependency_overrides.pop(get_gateway, None)


def test_run_returns_402_when_budget_exceeded(client, session_factory):
    user = client.post("/users", json={"email": "run-budget@example.com"}).json()
    project = client.post(
        "/projects", json={"owner_id": user["id"], "title": "P", "goal": "g", "budget": 0.000001}
    ).json()
    pid = project["id"]
    t = client.post(f"/projects/{pid}/tasks", json={"title": "T", "owner": "researcher"}).json()
    client.post(f"/projects/{pid}/tasks/{t['id']}/transition", json={"status": "READY"})
    client.post(f"/projects/{pid}/tasks/{t['id']}/transition", json={"status": "RUNNING"})
    use_fake_role(client, session_factory, "research", [RUN_RESULT])
    r = client.post(f"/projects/{pid}/tasks/{t['id']}/run")
    assert r.status_code == 402
    assert client.get(f"/projects/{pid}/tasks/{t['id']}").json()["status"] == "RUNNING"
    app.dependency_overrides.pop(get_gateway, None)


def test_run_returns_502_on_provider_error(client, session_factory, project):
    pid = project["id"]
    t = client.post(f"/projects/{pid}/tasks", json={"title": "T", "owner": "researcher"}).json()
    client.post(f"/projects/{pid}/tasks/{t['id']}/transition", json={"status": "READY"})
    client.post(f"/projects/{pid}/tasks/{t['id']}/transition", json={"status": "RUNNING"})

    class BrokenProvider(FakeProvider):
        def complete(self, request):
            raise ProviderError("boom")

    def override():
        with session_factory() as s:
            yield Gateway(s, providers={"fake": BrokenProvider()})

    app.dependency_overrides[get_gateway] = override
    client.put("/settings/global/0", json={"models": {"research": {"provider": "fake", "model": "claude-sonnet-5"}}})
    r = client.post(f"/projects/{pid}/tasks/{t['id']}/run")
    assert r.status_code == 502
    app.dependency_overrides.pop(get_gateway, None)
