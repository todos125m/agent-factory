"""A paused project gets no model calls and no task moves to READY/RUNNING on any manager path — plan,
approve, reject, checkpoint (incl. automatic mode's auto-decide), decide, run, chat — not only on the
transition endpoint. A refusal is HTTP 409 plus a `pause.blocked` event; a pause that lands while a
model call is in flight keeps the paid-for result but withholds its READY/RUNNING moves
(`pause.withheld`), and resuming releases withheld dependency promotions. Gateway.call refuses a paused
project's model call itself too, for a path that skips the manager's check. FakeProvider only.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from app import manager
from app.db import Base, SessionLocal, get_session, make_engine
from app.gateway.providers import FakeProvider
from app.gateway.service import Gateway
from app.main import app
from app.models import ModelCall, Project, Task
from app.registry import sync_from_files
from app.routers.manager import get_gateway
from app.routers.projects import set_paused
from app.schemas import ProjectPauseUpdate
from app.state_machine import ProjectPaused
from tests.test_manager import CHECKPOINT, _ready_task
from tests.test_task_run import PLAN, RUN_RESULT

CHAT_REPLY = {"reply": "On track.", "suggested_action": "none"}
ROUTE = {"provider": "fake", "model": "claude-opus-5"}


def _route_to_fake(client):
    client.put("/settings/global/0", json={"models": {"manager": ROUTE, "research": ROUTE}})


def use_provider(client, session_factory, provider):
    """Route the manager and research roles to `provider`; the gateway knows no other provider."""

    def override():
        with session_factory() as s:
            yield Gateway(s, providers={"fake": provider})

    app.dependency_overrides[get_gateway] = override
    _route_to_fake(client)
    return provider


def use_real_gateway(client, monkeypatch, provider):
    """Like use_provider, but through the real get_gateway (no override), as in production."""
    monkeypatch.setattr("app.gateway.service.default_providers", lambda: {"fake": provider})
    _route_to_fake(client)
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


# Every project route that makes a model call: (path, setup, request body, reply). plan/reject enters the call
# with its rejection staged in the same session, not yet written.
MODEL_CALL_PATHS = pytest.mark.parametrize("path, setup, body, reply", [
    ("plan", None, None, PLAN),
    ("plan/reject", None, {"feedback": "too shallow"}, PLAN),
    ("tasks/{tid}/checkpoint", _ready_task, None, CHECKPOINT),
    ("tasks/{tid}/run", _running_task, None, RUN_RESULT),
    ("chat", None, {"text": "status?"}, CHAT_REPLY),
], ids=["plan", "reject", "checkpoint", "run", "chat"])


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


# ---------- the gateway itself: a backstop for a path that skips ensure_active ----------


def test_gateway_refuses_a_paused_projects_call_before_the_provider(client, session, project):
    """A direct Gateway.call, as from a path that never ran ensure_active. `paused` is read fresh, not
    from a Project this session loaded before the owner paused in another session."""
    pid = project["id"]
    t = client.post(f"/projects/{pid}/tasks", json={"title": "T"}).json()
    loaded = session.get(Project, pid)  # like a router's load_project, before the pause
    _pause(client, pid)
    assert loaded.paused is False  # this session's copy is stale
    fake = FakeProvider(replies=[{"ok": True}])
    gw = Gateway(session, providers={"fake": fake})

    with pytest.raises(ProjectPaused, match="Project is paused"):
        gw.call("research", system="s", user="u", project_id=pid, task_id=t["id"], agent="researcher", route=ROUTE)
    assert fake.requests == [] and _model_calls(client, pid) == 0
    last = _events(client, pid)[-1]
    assert (last["type"], last["task_id"], last["payload"]) == (
        "pause.blocked", t["id"], {"action": "model_call", "role": "research", "agent": "researcher"},
    )

    _pause(client, pid, False)
    gw.call("research", system="s", user="u", project_id=pid, task_id=t["id"], agent="researcher", route=ROUTE)
    assert len(fake.requests) == 1 and _model_calls(client, pid) == 1


def test_gateway_refuses_a_task_scoped_call_that_names_no_project(client, session, project):
    pid = project["id"]
    t = client.post(f"/projects/{pid}/tasks", json={"title": "T"}).json()
    _pause(client, pid)
    fake = FakeProvider()

    with pytest.raises(ProjectPaused):
        Gateway(session, providers={"fake": fake}).call("research", system="s", user="u", task_id=t["id"], route=ROUTE)
    assert fake.requests == [] and session.query(ModelCall).count() == 0
    last = _events(client, pid)[-1]
    assert (last["type"], last["task_id"], last["payload"]["action"]) == ("pause.blocked", t["id"], "model_call")


def test_a_refusal_never_points_at_a_task_its_rollback_removed(client, session, project):
    """A task staged in the refused path's own uncommitted work is rolled back with it, so the event must not
    reference it (on PostgreSQL that foreign key would turn the 409 into a 500 with nothing recorded)."""
    pid = project["id"]
    _pause(client, pid)
    staged = Task(project_id=pid, title="staged")
    session.add(staged)
    session.flush()
    gw = Gateway(session, providers={"fake": FakeProvider()})

    with pytest.raises(ProjectPaused):
        gw.call("research", system="s", user="u", task_id=staged.id, route=ROUTE)
    assert client.get(f"/projects/{pid}/tasks").json() == []
    last = _events(client, pid)[-1]
    assert (last["type"], last["task_id"], last["payload"]["action"]) == ("pause.blocked", None, "model_call")


def test_gateway_still_calls_for_other_projects_and_without_a_project(client, session, project):
    """Only the paused project is refused: an unpaused project and a project-less call go through."""
    other = client.post("/projects", json={"owner_id": project["owner_id"], "title": "Other", "goal": "g"}).json()
    _pause(client, project["id"])
    fake = FakeProvider(replies=[{"n": 1}, {"n": 2}])
    gw = Gateway(session, providers={"fake": fake})

    assert gw.call("manager", system="s", user="u", project_id=other["id"], route=ROUTE).text == '{"n": 1}'
    assert gw.call("manager", system="s", user="u", agent="benchmark", route=ROUTE).text == '{"n": 2}'
    assert _model_calls(client, other["id"]) == 1 and session.query(ModelCall).count() == 2
    assert "pause.blocked" not in {e["type"] for p in (project, other) for e in _events(client, p["id"])}


def test_benchmark_runs_while_a_project_is_paused(client, session_factory, project):
    """The benchmark tab's calls carry no project, so a paused project never blocks them."""
    _pause(client, project["id"])
    use_provider(client, session_factory, FakeProvider(replies=[PLAN]))
    r = client.post("/benchmarks/run", json={"routes": [ROUTE]})
    assert r.status_code == 200, r.text
    assert (r.json()[0]["schema_valid"], r.json()[0]["error"]) == (True, None)
    app.dependency_overrides.pop(get_gateway, None)


@MODEL_CALL_PATHS
def test_a_path_that_skips_ensure_active_is_still_refused(client, project, monkeypatch, path, setup, body, reply):
    """A new project-scoped path that forgets ensure_active: the gateway refuses its model call, the router
    answers 409 as for any pause refusal, and nothing the path had started is kept."""
    pid = project["id"]
    tid = setup(client, pid)["id"] if setup else None
    fake = use_real_gateway(client, monkeypatch, FakeProvider(replies=[reply]))
    monkeypatch.setattr("app.manager.ensure_active", lambda *a, **kw: None)
    monkeypatch.setattr("app.chat.ensure_active", lambda *a, **kw: None)
    tasks = client.get(f"/projects/{pid}/tasks").json()
    _pause(client, pid)

    r = client.post(f"/projects/{pid}/{path.format(tid=tid)}", json=body)
    _assert_refused(client, pid, r, "model_call", tid)
    assert [e["type"] for e in _events(client, pid)][-2:] == ["project.paused", "pause.blocked"]  # e.g. no rejection
    assert fake.requests == []
    assert client.get(f"/projects/{pid}/tasks").json() == tasks
    assert client.get(f"/projects/{pid}/chat").json() == []  # chat stores the owner's message only with a reply (d19)


def test_a_route_that_does_not_map_the_refusal_still_answers_409(client, project, monkeypatch):
    """A path that neither checks the pause nor maps the refusal (no _run): app/main.py answers 409, not 500."""
    pid = project["id"]
    fake = use_real_gateway(client, monkeypatch, FakeProvider(replies=[PLAN]))
    monkeypatch.setattr("app.manager.ensure_active", lambda *a, **kw: None)
    monkeypatch.setattr("app.routers.manager._run", lambda fn, *a, **kw: fn(*a, **kw))
    _pause(client, pid)

    _assert_refused(client, pid, client.post(f"/projects/{pid}/plan"), "model_call")
    assert fake.requests == []


@pytest.mark.parametrize("skip_ensure_active, refused_by", [(False, "plan"), (True, "model_call")],
                         ids=["entry_check", "gateway"])
def test_a_pause_landing_mid_reject_keeps_the_plan(
    client, session_factory, project, monkeypatch, skip_ensure_active, refused_by
):
    """Reject and re-plan are one unit: when the owner pauses right after the rejection is staged, the
    re-plan is refused (by its entry check, or by the gateway on a path that skips it) and the rejection is
    rolled back with it — the plan is not deleted for nothing."""
    pid = project["id"]
    fake = use_real_gateway(client, monkeypatch, FakeProvider(replies=[PLAN, PLAN]))
    client.post(f"/projects/{pid}/plan")
    tasks = client.get(f"/projects/{pid}/tasks").json()
    reject_plan = manager.reject_plan

    def reject_then_the_owner_pauses(session, project, feedback):
        deleted = reject_plan(session, project, feedback)
        with session_factory() as s:  # the owner's pause request, in its own session
            set_paused(pid, ProjectPauseUpdate(paused=True), session=s)
        return deleted

    monkeypatch.setattr("app.manager.reject_plan", reject_then_the_owner_pauses)
    if skip_ensure_active:
        monkeypatch.setattr("app.manager.ensure_active", lambda *a, **kw: None)

    r = client.post(f"/projects/{pid}/plan/reject", json={"feedback": "too shallow"})
    _assert_refused(client, pid, r, refused_by, calls_before=1)
    assert len(fake.requests) == 1
    assert client.get(f"/projects/{pid}/tasks").json() == tasks
    assert "decision.plan_rejected" not in {e["type"] for e in _events(client, pid)}


def test_reject_and_replan_land_together_through_the_real_gateway(client, project, monkeypatch):
    """reject_plan leaves the commit to the re-plan; with the gateway on the route's own session (as in
    production) the rejection and the new plan both land."""
    pid = project["id"]
    fake = use_real_gateway(client, monkeypatch, FakeProvider(replies=[PLAN, {**PLAN, "understanding": "Revised"}]))
    client.post(f"/projects/{pid}/plan")
    old = {t["id"] for t in client.get(f"/projects/{pid}/tasks").json()}

    r = client.post(f"/projects/{pid}/plan/reject", json={"feedback": "too shallow"})
    assert r.status_code == 200 and r.json()["understanding"] == "Revised", r.text
    assert len(client.get(f"/projects/{pid}/tasks").json()) == 2  # SQLite may reuse the deleted ids
    rejected = [e for e in _events(client, pid) if e["type"] == "decision.plan_rejected"]
    assert len(rejected) == 1 and set(rejected[0]["payload"]["deleted_task_ids"]) == old
    assert len(fake.requests) == 2 and _model_calls(client, pid) == 2


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


@pytest.fixture
def file_client(tmp_path):
    """The app on a file-based SQLite database, like the default agent_factory.db, with production's session
    options (autoflush decides what a session writes before a model call): each session gets its own
    connection, so SQLite's database-wide write lock is real here (the other tests share one in-memory
    connection, which can't show it). Yields the client and its session factory."""
    engine = make_engine(f"sqlite:///{tmp_path / 'agent_factory.db'}")
    assert isinstance(engine.pool, QueuePool)  # one connection per session, even within one thread
    Base.metadata.create_all(engine)
    factory = sessionmaker(**{**SessionLocal.kw, "bind": engine})
    with factory() as s:
        sync_from_files(s)

    def override():
        with factory() as s:
            yield s

    app.dependency_overrides[get_session] = override
    yield TestClient(app), factory
    app.dependency_overrides.clear()
    engine.dispose()


@MODEL_CALL_PATHS
def test_a_pause_commits_while_a_model_call_is_in_flight_on_file_sqlite(
    file_client, monkeypatch, path, setup, body, reply
):
    """The reported bug: chat flushed the owner's message before its model call, and on SQLite that open write
    transaction held the database-wide write lock for the whole call, so the owner's pause (or any write) waited
    out the 5 s busy timeout and failed with "database is locked" (500). No model-call path may hold a write
    transaction while the provider runs, plan/reject's staged rejection included: the pause, on its own
    connection, commits mid-call."""
    client, factory = file_client
    user = client.post("/users", json={"email": "f@example.com"}).json()
    pid = client.post("/projects", json={"owner_id": user["id"], "title": "P", "goal": "g"}).json()["id"]
    tid = setup(client, pid)["id"] if setup else None
    use_real_gateway(client, monkeypatch, PausingProvider(factory, pid, [reply]))

    r = client.post(f"/projects/{pid}/{path.format(tid=tid)}", json=body)
    assert r.status_code == 200, r.text  # the call already in flight keeps its paid-for result (d16)
    assert client.get(f"/projects/{pid}").json()["paused"] is True
    assert "project.paused" in {e["type"] for e in _events(client, pid)}
    assert _model_calls(client, pid) == 1
    if path == "chat":  # stored after the reply, the owner's message with it (d19)
        assert [m["role"] for m in client.get(f"/projects/{pid}/chat").json()] == ["user", "manager"]
