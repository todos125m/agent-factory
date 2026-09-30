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

    # The plan is gone; its dependency-free task now waits to start (item 8).
    assert [i["kind"] for i in client.get("/inbox").json()] == ["ready"]
    app.dependency_overrides.pop(get_gateway, None)


def test_inbox_lists_checkpoint_awaiting_decision(client, session_factory, manual_project):
    pid = manual_project["id"]
    t = _ready_task(client, pid, risk="low")
    use_fake(client, session_factory, [CHECKPOINT])
    client.post(f"/projects/{pid}/tasks/{t['id']}/checkpoint")

    items = client.get("/inbox").json()
    assert len(items) == 1
    assert items[0]["kind"] == "checkpoint"
    assert items[0]["task_id"] == t["id"]
    assert items[0]["recommended"] == 0
    app.dependency_overrides.pop(get_gateway, None)


def test_inbox_drops_checkpoint_once_decided(client, session_factory, manual_project):
    pid = manual_project["id"]
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


# ---------- READY tasks with no checkpoint yet (night queue item 8) ----------


def _kinds(client):
    return [(i["kind"], i["task_id"]) for i in client.get("/inbox").json()]


def test_ready_task_without_checkpoint_is_listed_to_start(client, session_factory, manual_project):
    pid = manual_project["id"]
    register_researcher(client)
    use_fake(client, session_factory, [GOOD_PLAN])
    client.post(f"/projects/{pid}/plan")
    client.post(f"/projects/{pid}/plan/approve")
    research = next(t for t in client.get(f"/projects/{pid}/tasks").json() if t["title"] == "Research")
    became_ready = next(
        e for e in client.get(f"/projects/{pid}/events").json()
        if e["type"] == "task.status_changed" and e["task_id"] == research["id"] and e["payload"]["to"] == "READY"
    )

    assert client.get("/inbox").json() == [{  # "Customer" still waits on Research (CREATED): not listed
        "kind": "ready", "project_id": pid, "project_title": manual_project["title"], "task_id": research["id"],
        "challenge": "Research", "options": [{"title": "Research", "owner": "researcher", "risk": "low"}],
        "recommended": None, "why": "Find market pains", "created_at": became_ready["created_at"],
    }]
    app.dependency_overrides.pop(get_gateway, None)


def test_start_turns_a_ready_item_into_its_checkpoint(client, session_factory, manual_project):
    pid = manual_project["id"]
    t = _ready_task(client, pid, risk="low")
    assert _kinds(client) == [("ready", t["id"])]

    use_fake(client, session_factory, [CHECKPOINT])
    assert client.post(f"/projects/{pid}/tasks/{t['id']}/checkpoint").status_code == 200  # the inbox's «شروع»
    assert _kinds(client) == [("checkpoint", t["id"])]  # listed once, as the decision it now waits on
    app.dependency_overrides.pop(get_gateway, None)


def test_start_in_an_automatic_project_clears_the_item(client, session_factory, project):
    pid = project["id"]
    t = _ready_task(client, pid, risk="low")
    use_fake(client, session_factory, [CHECKPOINT])
    assert client.post(f"/projects/{pid}/tasks/{t['id']}/checkpoint").json()["auto_decided"] is True
    assert client.get("/inbox").json() == []  # RUNNING now: nothing left to start or decide
    app.dependency_overrides.pop(get_gateway, None)


def test_only_ready_tasks_are_listed(client, manual_project):
    pid = manual_project["id"]
    client.post(f"/projects/{pid}/tasks", json={"title": "still CREATED"})
    running = _ready_task(client, pid)
    client.post(f"/projects/{pid}/tasks/{running['id']}/transition", json={"status": "RUNNING"})
    cancelled = _ready_task(client, pid)
    client.post(f"/projects/{pid}/tasks/{cancelled['id']}/transition", json={"status": "CANCELLED"})
    assert client.get("/inbox").json() == []


def test_paused_project_ready_tasks_are_not_offered_to_start(client, manual_project):
    pid = manual_project["id"]
    t = _ready_task(client, pid)
    client.post(f"/projects/{pid}/pause", json={"paused": True})
    assert client.get("/inbox").json() == []
    client.post(f"/projects/{pid}/pause", json={"paused": False})
    assert _kinds(client) == [("ready", t["id"])]


def test_paused_project_hides_every_card_until_resume(client, session_factory, manual_project):
    """Owner decision d17: while paused, every action on these cards is refused, so none are listed."""
    pid = manual_project["id"]
    t = _ready_task(client, pid, risk="low")
    use_fake(client, session_factory, [CHECKPOINT, GOOD_PLAN])
    client.post(f"/projects/{pid}/tasks/{t['id']}/checkpoint")
    register_researcher(client)
    client.post(f"/projects/{pid}/plan")
    other = client.post("/projects", json={"owner_id": manual_project["owner_id"], "title": "Other", "goal": "g",
                                          "mode": "manual_learning"}).json()
    other_task = _ready_task(client, other["id"])
    listed = _kinds(client)
    assert listed == [("checkpoint", t["id"]), ("plan", None), ("ready", other_task["id"])]

    client.post(f"/projects/{pid}/pause", json={"paused": True})
    assert _kinds(client) == [("ready", other_task["id"])]  # only the paused project's cards go
    client.post(f"/projects/{pid}/pause", json={"paused": False})
    assert _kinds(client) == listed
    app.dependency_overrides.pop(get_gateway, None)


def test_retried_task_is_listed_again(client, session_factory, manual_project):
    """FAILED -> READY after a decided checkpoint: no pending checkpoint, so it waits to start again."""
    pid = manual_project["id"]
    t = _ready_task(client, pid)
    use_fake(client, session_factory, [CHECKPOINT])
    client.post(f"/projects/{pid}/tasks/{t['id']}/checkpoint")
    client.post(f"/projects/{pid}/tasks/{t['id']}/decide", json={"option": 0})
    for status in ("FAILED", "READY"):
        assert client.post(f"/projects/{pid}/tasks/{t['id']}/transition", json={"status": status}).status_code == 200
    assert _kinds(client) == [("ready", t["id"])]
    app.dependency_overrides.pop(get_gateway, None)


def test_checkpoint_left_undecided_before_a_retry_is_stale(client, session_factory, manual_project):
    pid = manual_project["id"]
    t = _ready_task(client, pid)
    use_fake(client, session_factory, [CHECKPOINT])
    client.post(f"/projects/{pid}/tasks/{t['id']}/checkpoint")  # opened, never decided
    for status in ("RUNNING", "FAILED", "READY"):  # moved on by hand, failed, retried
        assert client.post(f"/projects/{pid}/tasks/{t['id']}/transition", json={"status": status}).status_code == 200
    assert _kinds(client) == [("ready", t["id"])]  # waits to start again, not on the old options
    app.dependency_overrides.pop(get_gateway, None)


def test_items_of_every_kind_are_ordered_by_time(client, session_factory, manual_project):
    pid = manual_project["id"]
    ready = _ready_task(client, pid, risk="low")
    register_researcher(client)
    use_fake(client, session_factory, [GOOD_PLAN])
    client.post(f"/projects/{pid}/plan")
    assert _kinds(client) == [("ready", ready["id"]), ("plan", None)]
    app.dependency_overrides.pop(get_gateway, None)
