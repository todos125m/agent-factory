"""A paused project gets no model calls and no task moves to READY/RUNNING on any manager path — plan,
approve, reject, checkpoint (incl. automatic mode's auto-decide), decide, run, chat — not only on the
transition endpoint. A refusal is HTTP 409 plus a `pause.blocked` event; a pause that lands while a
model call is in flight keeps the paid-for result but withholds its READY/RUNNING moves
(`pause.withheld`), and resuming releases withheld dependency promotions. FakeProvider only.
"""

import pytest

from app.gateway.providers import FakeProvider
from app.gateway.service import Gateway
from app.main import app
from app.routers.manager import get_gateway
from app.routers.projects import set_paused
from app.schemas import ProjectPauseUpdate
from tests.test_manager import CHECKPOINT, _ready_task
from tests.test_task_run import PLAN, RUN_RESULT

CHAT_REPLY = {"reply": "On track.", "suggested_action": "none"}


def use_provider(client, session_factory, provider):
    """Route the manager and research roles to `provider`; the gateway knows no other provider."""

    def override():
        with session_factory() as s:
            yield Gateway(s, providers={"fake": provider})

    app.dependency_overrides[get_gateway] = override
    route = {"provider": "fake", "model": "claude-opus-5"}
    client.put("/settings/global/0", json={"models": {"manager": route, "research": route}})
    return provider


class PausingProvider(FakeProvider):
    """Replies like FakeProvider, but the owner pauses the project while the call is in flight."""

    def __init__(self, session_factory, project_id, replies):
        super().__init__(replies=list(replies))
        self.session_factory, self.project_id = session_factory, project_id

    def complete(self, request):
        with self.session_factory() as s:  # the owner's pause request, in its own session
            set_paused(self.project_id, ProjectPauseUpdate(paused=True), session=s)
        return super().complete(request)


def _pause(client, pid, paused=True):
    r = client.post(f"/projects/{pid}/pause", json={"paused": paused})
    assert r.status_code == 200 and r.json()["paused"] is paused


def _events(client, pid):
    return client.get(f"/projects/{pid}/events").json()


def _status(client, pid, tid):
    return client.get(f"/projects/{pid}/tasks/{tid}").json()["status"]


def _model_calls(client, pid):
    return len(client.get(f"/projects/{pid}/model_calls").json())


def _assert_refused(client, pid, response, action, task_id=None, *, calls_before=0):
    """409 like the transition endpoint, nothing spent, and the refusal is on the record."""
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "Project is paused"
    last = _events(client, pid)[-1]
    assert (last["type"], last["task_id"], last["payload"]["action"]) == ("pause.blocked", task_id, action)
    assert _model_calls(client, pid) == calls_before


def _running_task(client, pid):
    t = client.post(f"/projects/{pid}/tasks", json={"title": "T", "owner": "researcher"}).json()
    for status in ("READY", "RUNNING"):
        client.post(f"/projects/{pid}/tasks/{t['id']}/transition", json={"status": status})
    return t


def _approved_plan(client, pid):
    """PLAN approved: "Research" READY, "Define product" CREATED (depends on Research)."""
    client.post(f"/projects/{pid}/plan")
    client.post(f"/projects/{pid}/plan/approve")
    tasks = {t["title"]: t for t in client.get(f"/projects/{pid}/tasks").json()}
    return tasks["Research"], tasks["Define product"]


# ---------- every manager path, paused: 409 before any model call or state change ----------


def test_paused_project_is_not_planned(client, session_factory, project):
    pid = project["id"]
    fake = use_provider(client, session_factory, FakeProvider(replies=[PLAN]))
    _pause(client, pid)

    _assert_refused(client, pid, client.post(f"/projects/{pid}/plan"), "plan")
    assert fake.requests == []
    assert client.get(f"/projects/{pid}/tasks").json() == []
    app.dependency_overrides.pop(get_gateway, None)


def test_paused_project_plan_is_not_approved(client, session_factory, project):
    pid = project["id"]
    use_provider(client, session_factory, FakeProvider(replies=[PLAN]))
    client.post(f"/projects/{pid}/plan")
    _pause(client, pid)

    _assert_refused(client, pid, client.post(f"/projects/{pid}/plan/approve"), "plan.approve", calls_before=1)
    assert {t["status"] for t in client.get(f"/projects/{pid}/tasks").json()} == {"CREATED"}
    assert "decision.plan_approved" not in {e["type"] for e in _events(client, pid)}
    app.dependency_overrides.pop(get_gateway, None)


def test_paused_project_plan_is_not_rejected_or_replanned(client, session_factory, project):
    """Refused up front: rejecting first and then failing the re-plan would delete the plan for nothing."""
    pid = project["id"]
    fake = use_provider(client, session_factory, FakeProvider(replies=[PLAN, PLAN]))
    client.post(f"/projects/{pid}/plan")
    tasks = client.get(f"/projects/{pid}/tasks").json()
    _pause(client, pid)

    r = client.post(f"/projects/{pid}/plan/reject", json={"feedback": "too shallow"})
    _assert_refused(client, pid, r, "plan.reject", calls_before=1)
    assert client.get(f"/projects/{pid}/tasks").json() == tasks
    assert len(fake.requests) == 1
    assert "decision.plan_rejected" not in {e["type"] for e in _events(client, pid)}
    app.dependency_overrides.pop(get_gateway, None)


@pytest.mark.parametrize("project_fixture", ["project", "manual_project"])
def test_paused_project_gets_no_checkpoint(client, session_factory, request, project_fixture):
    """The reported bug: "automatic" is the API default, so a paused project's checkpoint spent a model
    call and auto-decided READY -> RUNNING. manual_learning spent the call too."""
    pid = request.getfixturevalue(project_fixture)["id"]
    t = _ready_task(client, pid, risk="low")
    fake = use_provider(client, session_factory, FakeProvider(replies=[CHECKPOINT]))
    _pause(client, pid)

    _assert_refused(client, pid, client.post(f"/projects/{pid}/tasks/{t['id']}/checkpoint"), "checkpoint", t["id"])
    assert fake.requests == []
    assert _status(client, pid, t["id"]) == "READY"
    assert not {"checkpoint.created", "decision.step"} & {e["type"] for e in _events(client, pid)}
    app.dependency_overrides.pop(get_gateway, None)


def test_paused_project_decision_is_refused(client, session_factory, manual_project):
    pid = manual_project["id"]
    t = _ready_task(client, pid, risk="low")
    use_provider(client, session_factory, FakeProvider(replies=[CHECKPOINT]))
    client.post(f"/projects/{pid}/tasks/{t['id']}/checkpoint")
    _pause(client, pid)

    r = client.post(f"/projects/{pid}/tasks/{t['id']}/decide", json={"option": 0})
    _assert_refused(client, pid, r, "decide", t["id"], calls_before=1)
    assert _status(client, pid, t["id"]) == "READY"
    assert "decision.step" not in {e["type"] for e in _events(client, pid)}
    app.dependency_overrides.pop(get_gateway, None)


def test_paused_project_task_is_not_run(client, session_factory, project):
    pid = project["id"]
    t = _running_task(client, pid)
    fake = use_provider(client, session_factory, FakeProvider(replies=[RUN_RESULT]))
    _pause(client, pid)

    _assert_refused(client, pid, client.post(f"/projects/{pid}/tasks/{t['id']}/run"), "run", t["id"])
    assert fake.requests == []
    task = client.get(f"/projects/{pid}/tasks/{t['id']}").json()
    assert task["status"] == "RUNNING" and task["output"] is None
    app.dependency_overrides.pop(get_gateway, None)


def test_paused_project_chat_is_refused(client, session_factory, project):
    pid = project["id"]
    fake = use_provider(client, session_factory, FakeProvider(replies=[CHAT_REPLY]))
    _pause(client, pid)

    _assert_refused(client, pid, client.post(f"/projects/{pid}/chat", json={"text": "status?"}), "chat")
    assert fake.requests == []
    assert client.get(f"/projects/{pid}/chat").json() == []  # reading still works; the message was not stored
    app.dependency_overrides.pop(get_gateway, None)


def test_transition_and_stage_refusals_go_through_the_same_check(client, project):
    pid = project["id"]
    t = client.post(f"/projects/{pid}/tasks", json={"title": "T"}).json()
    _pause(client, pid)

    r = client.post(f"/projects/{pid}/tasks/{t['id']}/transition", json={"status": "READY"})
    _assert_refused(client, pid, r, "transition", t["id"])
    assert _events(client, pid)[-1]["payload"]["to"] == "READY"
    _assert_refused(client, pid, client.post(f"/projects/{pid}/stage", json={"stage": "DISCOVERY"}), "stage")
    # A move that starts nothing is still allowed while paused, as before.
    assert client.post(f"/projects/{pid}/tasks/{t['id']}/transition", json={"status": "CANCELLED"}).status_code == 200


# ---------- unpaused happy path: after a resume nothing is stuck ----------


def test_every_path_works_after_resume(client, session_factory, project):
    pid = project["id"]
    fake = use_provider(client, session_factory, FakeProvider(replies=[PLAN, PLAN, CHECKPOINT, RUN_RESULT, CHAT_REPLY]))
    _pause(client, pid)
    assert client.post(f"/projects/{pid}/plan").status_code == 409
    _pause(client, pid, False)

    assert client.post(f"/projects/{pid}/plan").status_code == 200
    assert client.post(f"/projects/{pid}/plan/reject", json={"feedback": "again"}).status_code == 200
    assert client.post(f"/projects/{pid}/plan/approve").json()["ready_task_ids"]
    tasks = {t["title"]: t for t in client.get(f"/projects/{pid}/tasks").json()}
    research, product = tasks["Research"], tasks["Define product"]
    r = client.post(f"/projects/{pid}/tasks/{research['id']}/checkpoint")
    assert r.status_code == 200 and r.json()["auto_decided"] is True
    r = client.post(f"/projects/{pid}/tasks/{research['id']}/run")
    assert r.status_code == 200 and r.json()["ready_task_ids"] == [product["id"]]
    assert client.post(f"/projects/{pid}/chat", json={"text": "status?"}).status_code == 200

    assert len(fake.requests) == 5 and _model_calls(client, pid) == 5
    assert [e["type"] for e in _events(client, pid)].count("pause.blocked") == 1
    app.dependency_overrides.pop(get_gateway, None)


# ---------- a pause that lands while a model call is in flight ----------


def test_pause_during_checkpoint_call_withholds_the_auto_decision(client, session_factory, project):
    pid = project["id"]
    t = _ready_task(client, pid, risk="low")
    use_provider(client, session_factory, PausingProvider(session_factory, pid, [CHECKPOINT]))

    r = client.post(f"/projects/{pid}/tasks/{t['id']}/checkpoint")
    assert r.status_code == 200, r.text
    assert r.json()["auto_decided"] is False  # the paid-for checkpoint is kept, the decision is not taken
    assert _status(client, pid, t["id"]) == "READY"
    events = _events(client, pid)
    assert [e["type"] for e in events[-3:]] == ["project.paused", "checkpoint.created", "pause.withheld"]
    assert events[-1]["task_id"] == t["id"] and events[-1]["payload"] == {"action": "auto_decide"}
    assert "decision.step" not in {e["type"] for e in events}

    # It now waits for the owner like any checkpoint; the decision goes through once resumed. While
    # paused the inbox hides the project's cards (owner decision d17); resume brings the checkpoint back.
    assert client.get("/inbox").json() == []
    assert client.post(f"/projects/{pid}/tasks/{t['id']}/decide", json={"option": 0}).status_code == 409
    _pause(client, pid, False)
    assert [(i["kind"], i["task_id"]) for i in client.get("/inbox").json()] == [("checkpoint", t["id"])]
    assert _status(client, pid, t["id"]) == "READY"  # resume does not take the decision for the owner
    assert client.post(f"/projects/{pid}/tasks/{t['id']}/decide", json={"option": 0}).json()["status"] == "RUNNING"
    app.dependency_overrides.pop(get_gateway, None)


def test_pause_during_run_keeps_the_result_and_withholds_dependents_until_resume(client, session_factory, project):
    pid = project["id"]
    use_provider(client, session_factory, FakeProvider(replies=[PLAN, CHECKPOINT]))
    research, product = _approved_plan(client, pid)
    client.post(f"/projects/{pid}/tasks/{research['id']}/checkpoint")  # automatic: auto-decided
    assert _status(client, pid, research["id"]) == "RUNNING"
    unrelated = client.post(f"/projects/{pid}/tasks", json={"title": "not part of the plan"}).json()

    use_provider(client, session_factory, PausingProvider(session_factory, pid, [RUN_RESULT]))
    r = client.post(f"/projects/{pid}/tasks/{research['id']}/run")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["task"]["status"] == "COMPLETED"  # the paid-for result is kept
    assert body["task"]["output"]["summary"] == RUN_RESULT["summary"]
    assert body["ready_task_ids"] == []
    assert _status(client, pid, product["id"]) == "CREATED"
    withheld = _events(client, pid)[-1]
    assert (withheld["type"], withheld["task_id"], withheld["payload"]) == (
        "pause.withheld", research["id"], {"action": "promote_dependents"},
    )

    _pause(client, pid, False)
    assert _status(client, pid, product["id"]) == "READY"
    assert _status(client, pid, unrelated["id"]) == "CREATED"  # resume releases only what was withheld
    promoted = _events(client, pid)[-1]
    assert (promoted["type"], promoted["task_id"]) == ("task.status_changed", product["id"])
    assert promoted["payload"] == {"from": "CREATED", "to": "READY", "reason": "project resumed"}

    # Released once: another pause/resume cycle changes nothing.
    seen = len(_events(client, pid))
    _pause(client, pid)
    _pause(client, pid, False)
    assert [e["type"] for e in _events(client, pid)[seen:]] == ["project.paused", "project.resumed"]
    app.dependency_overrides.pop(get_gateway, None)


def test_resume_keeps_a_dependent_whose_other_dependency_is_unfinished(client, session_factory, project):
    pid = project["id"]
    a, b = _running_task(client, pid), _running_task(client, pid)
    c = client.post(f"/projects/{pid}/tasks", json={"title": "C", "depends_on": [a["id"], b["id"]]}).json()
    use_provider(client, session_factory, PausingProvider(session_factory, pid, [RUN_RESULT]))
    assert client.post(f"/projects/{pid}/tasks/{a['id']}/run").json()["ready_task_ids"] == []

    _pause(client, pid, False)
    assert _status(client, pid, c["id"]) == "CREATED"  # b is still RUNNING
    use_provider(client, session_factory, FakeProvider(replies=[RUN_RESULT]))
    assert client.post(f"/projects/{pid}/tasks/{b['id']}/run").json()["ready_task_ids"] == [c["id"]]
    app.dependency_overrides.pop(get_gateway, None)


def test_pause_during_run_of_a_leaf_task_withholds_nothing(client, session_factory, project):
    pid = project["id"]
    t = _running_task(client, pid)
    use_provider(client, session_factory, PausingProvider(session_factory, pid, [RUN_RESULT]))
    r = client.post(f"/projects/{pid}/tasks/{t['id']}/run")
    assert r.status_code == 200 and r.json()["task"]["status"] == "COMPLETED"
    assert "pause.withheld" not in {e["type"] for e in _events(client, pid)}
    app.dependency_overrides.pop(get_gateway, None)
