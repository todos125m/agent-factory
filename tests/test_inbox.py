from app.main import app
from app.routers.manager import get_gateway
from tests.test_manager import CHECKPOINT, GOOD_PLAN, _ready_task, register_researcher, use_fake


def test_inbox_empty_when_nothing_pending(client):
    assert client.get("/inbox").json() == []


def test_inbox_lists_plan_awaiting_approval(client, session_factory, project):
    pid = project["id"]
    register_researcher(client)
    use_fake(client, session_factory, [GOOD_PLAN])
    client.post(f"/projects/{pid}/plan")

    items = client.get("/inbox").json()
    assert len(items) == 1
    assert items[0]["kind"] == "plan"
    assert items[0]["project_id"] == pid
    assert items[0]["challenge"] == GOOD_PLAN["understanding"]
    assert {o["title"] for o in items[0]["options"]} == {"Research", "Customer"}
    app.dependency_overrides.pop(get_gateway, None)


def test_inbox_drops_plan_once_approved(client, session_factory, project):
    pid = project["id"]
    register_researcher(client)
    use_fake(client, session_factory, [GOOD_PLAN])
    client.post(f"/projects/{pid}/plan")
    client.post(f"/projects/{pid}/plan/approve")

    assert client.get("/inbox").json() == []
    app.dependency_overrides.pop(get_gateway, None)


def test_inbox_lists_checkpoint_awaiting_decision(client, session_factory, project):
    pid = project["id"]
    t = _ready_task(client, pid, risk="low")
    use_fake(client, session_factory, [CHECKPOINT])
    client.post(f"/projects/{pid}/tasks/{t['id']}/checkpoint")

    items = client.get("/inbox").json()
    assert len(items) == 1
    assert items[0]["kind"] == "checkpoint"
    assert items[0]["task_id"] == t["id"]
    assert items[0]["recommended"] == 0
    app.dependency_overrides.pop(get_gateway, None)


def test_inbox_drops_checkpoint_once_decided(client, session_factory, project):
    pid = project["id"]
    t = _ready_task(client, pid, risk="low")
    use_fake(client, session_factory, [CHECKPOINT])
    client.post(f"/projects/{pid}/tasks/{t['id']}/checkpoint")
    client.post(f"/projects/{pid}/tasks/{t['id']}/decide", json={"option": 0})

    assert client.get("/inbox").json() == []
    app.dependency_overrides.pop(get_gateway, None)


def test_inbox_omits_auto_decided_checkpoint(client, session_factory, project):
    pid = project["id"]
    client.put(f"/settings/project/{pid}", json={"mode": "automatic"})
    t = _ready_task(client, pid, risk="low")
    use_fake(client, session_factory, [CHECKPOINT])
    client.post(f"/projects/{pid}/tasks/{t['id']}/checkpoint")

    assert client.get("/inbox").json() == []
    app.dependency_overrides.pop(get_gateway, None)
