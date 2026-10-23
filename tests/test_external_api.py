"""The external API (app/external.py, issue #2 PR7; owner decisions d42–d44): a key the owner makes is a standing approval
of one activated agent version of their own, one project, scopes and limits. Calls run inside it; the caller can answer
the agent's questions and nothing else. FakeProvider only."""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app import external, worker
from app.gateway.providers import FakeProvider
from app.gateway.service import Gateway
from app.main import app
from app.models import Agent, ApiKey, Run, SettingsLayer, Task, TaskStatus, utcnow
from tests.test_factory_build import act, fake_models, passed
from tests.test_factory_intake import rid
from tests.test_task_run import RUN_RESULT

FINISH = {"action": "finish", "reason": "done", "result": RUN_RESULT}
ASK = {"action": "ask_user", "reason": "unclear", "question": "Which month?"}


@pytest.fixture
def ext():
    return TestClient(app)  # no cookie: only the key


def activated(client, session_factory):
    """The owner's agent 'ticket-triage' v1: built, evaluated (PASS) and activated; its project exists."""
    st = passed(client, session_factory)
    assert act(client, st, "activate", agent_version=1).status_code == 200
    me = client.get("/auth/me").json()
    return client.post("/projects", json={"owner_id": me["id"], "title": "API project", "goal": "g"}).json()


def make_key(client, project, **kw):
    body = {"name": "CI", "agent": "ticket-triage", "agent_version": 1, "project_id": project["id"],
            "request_id": rid(), **kw}
    r = client.post("/api-keys", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def bearer(secret):
    return {"Authorization": f"Bearer {secret}"}


def call(ext, secret, method, path, **body):
    return ext.request(method, f"/api/v1{path}", headers=bearer(secret), json=body or None)


def step(session_factory, replies):
    fake = FakeProvider(replies=list(replies))
    worker.work_once(session_factory, "w1", lambda s: Gateway(s, providers={"fake": fake}))
    return fake


@pytest.fixture
def setup(client, session_factory, ext):
    project = activated(client, session_factory)
    fake_models(client, session_factory, [])  # the agent's role and the judge's go to the fake provider
    key = make_key(client, project)
    return project, key, key["secret"]


# ---------- making keys ----------


def test_a_key_is_made_once_with_its_secret_and_only_its_hash_is_kept(client, session_factory, ext):
    project = activated(client, session_factory)
    key = make_key(client, project)
    assert key["secret"].startswith("afk_") and key["prefix"] == key["secret"][:8] and key["duplicate"] is False
    assert (key["scopes"], key["max_runs_per_day"], key["budget_per_run_usd"]) == (
        ["answer", "read", "run"], 20, 0.5)
    listing = client.get("/api-keys").json()
    assert [k["id"] for k in listing["items"]] == [key["id"]] and "secret" not in json.dumps(listing)
    assert key["secret"] not in json.dumps(listing) and [a["name"] for a in listing["agents"]] == ["ticket-triage"]
    with session_factory() as s:
        row = s.get(ApiKey, key["id"])
        assert row.token_hash == external._digest(key["secret"]) and key["secret"] not in json.dumps(
            [row.token_hash, row.prefix, row.name])
    me = ext.get("/api/v1/me", headers=bearer(key["secret"])).json()
    assert me["agent"] == "ticket-triage" and me["runs_today"] == 0 and "secret" not in me


def test_a_replayed_make_returns_the_key_without_a_second_secret(client, session_factory):
    project = activated(client, session_factory)
    same = rid()
    first = make_key(client, project, request_id=same)
    again = client.post("/api-keys", json={"name": "CI", "agent": "ticket-triage", "agent_version": 1,
                                           "project_id": project["id"], "request_id": same}).json()
    assert again["id"] == first["id"] and again["secret"] is None and again["duplicate"] is True


@pytest.mark.parametrize("override, code, status", [
    ({"agent": "researcher", "agent_version": 2}, "agent_unavailable", 409),  # a registry agent, not one they built
    ({"agent_version": 9}, "agent_unavailable", 409),
    ({"scopes": []}, "api_key_invalid", 422),
    ({"scopes": ["run", "approve"]}, "api_key_invalid", 422),
    ({"max_runs_per_day": 0}, "api_key_invalid", 422),
    ({"max_runs_per_day": 10_001}, "api_key_invalid", 422),
    ({"budget_per_run_usd": 0}, "api_key_invalid", 422),
    ({"budget_per_run_usd": 101}, "api_key_invalid", 422),
])
def test_a_key_can_only_pin_an_activated_agent_the_owner_built_within_sane_limits(
        client, session_factory, override, code, status):
    project = activated(client, session_factory)
    r = client.post("/api-keys", json={"name": "CI", "agent": "ticket-triage", "agent_version": 1,
                                       "project_id": project["id"], "request_id": rid(), **override})
    assert r.status_code == status and r.json()["code"] == code, r.text


def test_an_agent_that_passed_but_was_never_activated_cannot_be_pinned(client, session_factory):
    from tests.test_factory_build import passed as only_passed

    only_passed(client, session_factory)  # PASS, no activation
    me = client.get("/auth/me").json()
    project = client.post("/projects", json={"owner_id": me["id"], "title": "P", "goal": "g"}).json()
    r = client.post("/api-keys", json={"name": "CI", "agent": "ticket-triage", "agent_version": 1,
                                       "project_id": project["id"], "request_id": rid()})
    assert r.status_code == 409 and r.json()["code"] == "agent_unavailable"


def test_another_accounts_project_or_key_is_invisible(client, session_factory):
    project = activated(client, session_factory)
    key = make_key(client, project)
    client.post("/users", json={"email": "other@example.com"})
    assert client.get("/api-keys").json()["items"] == []
    assert client.delete(f"/api-keys/{key['id']}").status_code == 404
    r = client.post("/api-keys", json={"name": "x", "agent": "ticket-triage", "agent_version": 1,
                                       "project_id": project["id"], "request_id": rid()})
    assert r.status_code == 404  # not their project


# ---------- the key as the only credential ----------


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer"},
                                     {"Authorization": "Bearer afk_notarealkey"}, {"Authorization": "Bearer sk-other"}])
def test_every_wrong_key_gets_the_same_401(client, ext, headers):
    r = ext.get("/api/v1/me", headers=headers)
    assert r.status_code == 401 and r.json()["code"] == "invalid_api_key"


def test_a_revoked_key_stops_at_once_and_looks_like_an_unknown_one(client, session_factory, ext, setup):
    project, key, secret = setup
    unknown = ext.get("/api/v1/me", headers=bearer("afk_" + "x" * 43))
    assert client.delete(f"/api-keys/{key['id']}").json()["revoked"] is True
    revoked = ext.get("/api/v1/me", headers=bearer(secret))
    assert revoked.status_code == 401 and revoked.json() == unknown.json()
    assert client.get("/api-keys").json()["items"] == []


def test_the_session_cookie_is_no_substitute_for_a_key(client, setup, ext):
    assert ext.get("/api/v1/me").status_code == 401  # no key
    assert client.get("/api/v1/me").status_code == 401  # a signed-in owner without a key


# ---------- a run ----------


def test_a_run_goes_from_the_call_to_a_result_inside_the_keys_limits(client, session_factory, ext, setup):
    project, key, secret = setup
    r = call(ext, secret, "POST", "/runs", input="Charged twice for March", request_id="call-0001")
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["state"] == "queued" and created["duplicate"] is False and created["result"] is None
    run_id = created["run_id"]
    with session_factory() as s:
        run = s.get(Run, run_id)
        task = s.get(Task, run.task_id)
        assert (run.api_key_id, run.project_id, run.agent, run.agent_version) == (key["id"], project["id"], "ticket-triage", 1)
        assert task.owner == "ticket-triage" and task.input["goal"] == "Charged twice for March"
        layer = s.scalar(select(SettingsLayer).where(SettingsLayer.scope_id == task.id))
        assert layer.values == {"budget": {"task_usd": 0.5}}  # the key's budget per run
    step(session_factory, [FINISH])
    done = call(ext, secret, "GET", f"/runs/{run_id}").json()
    assert done["state"] == "completed" and done["result"]["summary"] == RUN_RESULT["summary"]
    assert done["waiting_for"] is None and "steps" not in done and "pending" not in done
    assert call(ext, secret, "GET", "/me").json()["runs_today"] == 1


def test_the_same_request_id_returns_the_same_run_once(client, session_factory, ext, setup):
    project, key, secret = setup
    first = call(ext, secret, "POST", "/runs", input="hello", request_id="same-call-1").json()
    again = call(ext, secret, "POST", "/runs", input="hello", request_id="same-call-1")
    assert again.status_code == 201 and again.json()["run_id"] == first["run_id"] and again.json()["duplicate"] is True
    with session_factory() as s:
        assert s.query(Run).count() == 1 and s.query(Task).count() == 1


def test_the_agent_can_ask_and_only_the_caller_can_answer_questions(client, session_factory, ext, setup):
    project, key, secret = setup
    run_id = call(ext, secret, "POST", "/runs", input="hello", request_id="call-ask-01").json()["run_id"]
    step(session_factory, [ASK])
    waiting = call(ext, secret, "GET", f"/runs/{run_id}").json()
    assert (waiting["state"], waiting["waiting_for"], waiting["question"]) == ("waiting", "answer", "Which month?")
    answered = call(ext, secret, "POST", f"/runs/{run_id}/answer", text="March")
    assert answered.status_code == 200 and answered.json()["state"] == "running"
    step(session_factory, [FINISH])
    assert call(ext, secret, "GET", f"/runs/{run_id}").json()["state"] == "completed"
    again = call(ext, secret, "POST", f"/runs/{run_id}/answer", text="April")
    assert again.status_code == 409 and again.json()["code"] == "not_waiting_for_answer"


def test_a_tool_or_unknown_outcome_waits_for_the_owner_and_the_caller_cannot_settle_it(client, session_factory, ext, setup):
    project, key, secret = setup
    run_id = call(ext, secret, "POST", "/runs", input="hello", request_id="call-tool-1").json()["run_id"]
    with session_factory() as s:  # as the runtime leaves a run that wants to use a tool
        run = s.get(Run, run_id)
        run.state, run.reason = "waiting", "tool_approval"
        run.pending = {"kind": "tool_approval", "tool": "memory_search", "input": {"query": "customer secrets"}}
        s.get(Task, run.task_id).status = TaskStatus.WAITING
        s.commit()
    seen = call(ext, secret, "GET", f"/runs/{run_id}")
    assert seen.json()["waiting_for"] == "owner" and seen.json()["question"] is None
    assert "customer secrets" not in seen.text and "memory_search" not in seen.text  # the tool's input is the owner's
    for body in ({"text": "yes, approved"}, {"approve": True}):
        r = call(ext, secret, "POST", f"/runs/{run_id}/answer", **body)
        assert r.status_code in (409, 422)
    with session_factory() as s:
        assert s.get(Run, run_id).state == "waiting"  # nothing the caller sent moved it
    # the owner, signed in, can still settle it
    task_id = client.get(f"/projects/{project['id']}/tasks").json()[0]["id"]
    r = client.post(f"/projects/{project['id']}/tasks/{task_id}/runs/{run_id}/respond", json={"approve": False})
    assert r.status_code == 200


def test_a_run_can_be_cancelled_with_the_run_scope(client, session_factory, ext, setup):
    project, key, secret = setup
    run_id = call(ext, secret, "POST", "/runs", input="hello", request_id="call-cancel").json()["run_id"]
    r = call(ext, secret, "POST", f"/runs/{run_id}/cancel")
    assert r.status_code == 200 and (r.json()["state"], r.json()["reason"]) == ("stopped", "run_cancelled")


# ---------- scopes ----------


@pytest.mark.parametrize("scopes, method, path, body, ok", [
    (["read"], "POST", "/runs", {"input": "x", "request_id": "scope-run-1"}, False),
    (["run"], "GET", "/runs/1", None, False),
    (["run", "read"], "POST", "/runs/1/answer", {"text": "x"}, False),
    (["read", "answer"], "POST", "/runs/1/cancel", None, False),
])
def test_a_key_does_only_what_its_scopes_say(client, session_factory, ext, setup, scopes, method, path, body, ok):
    project, _, _ = setup
    narrow = make_key(client, project, scopes=scopes)
    r = ext.request(method, f"/api/v1{path}", headers=bearer(narrow["secret"]), json=body)
    assert r.status_code == 403 and r.json()["code"] == "api_scope_missing", r.text


# ---------- limits, all before any spend ----------


def test_the_daily_quota_refuses_the_next_call_before_it_costs_anything(client, session_factory, ext, setup):
    project, _, _ = setup
    narrow = make_key(client, project, max_runs_per_day=1)
    secret = narrow["secret"]
    assert call(ext, secret, "POST", "/runs", input="a", request_id="quota-call-1").status_code == 201
    r = call(ext, secret, "POST", "/runs", input="b", request_id="quota-call-2")
    assert r.status_code == 429 and r.json()["code"] == "quota_exceeded" and r.json()["params"]["limit"] == 1
    with session_factory() as s:
        assert s.query(Task).count() == 1 and s.query(Run).count() == 1
        s.execute(update(Run).values(created_at=utcnow() - external.WINDOW))  # a day later
        s.commit()
    assert call(ext, secret, "POST", "/runs", input="c", request_id="quota-call-3").status_code == 201


def test_at_most_three_runs_of_one_key_are_open(client, session_factory, ext, setup):
    project, _, secret = setup
    for i in range(3):
        assert call(ext, secret, "POST", "/runs", input="x", request_id=f"open-call-0{i}").status_code == 201
    r = call(ext, secret, "POST", "/runs", input="x", request_id="open-call-09")
    assert r.status_code == 429 and r.json()["code"] == "too_many_open_runs"


def test_a_paused_project_refuses_and_changes_nothing(client, session_factory, ext, setup):
    project, _, secret = setup
    client.post(f"/projects/{project['id']}/pause", json={"paused": True})
    r = call(ext, secret, "POST", "/runs", input="x", request_id="paused-call1")
    assert r.status_code == 409 and r.json()["code"] == "project_paused"
    with session_factory() as s:
        assert s.query(Run).count() == 0 and s.query(Task).count() == 0


def test_an_agent_that_is_no_longer_the_approved_version_stops_the_key(client, session_factory, ext, setup):
    project, _, secret = setup
    with session_factory() as s:
        s.execute(update(Agent).where(Agent.name == "ticket-triage").values(version=2))  # another version took over
        s.commit()
    r = call(ext, secret, "POST", "/runs", input="x", request_id="changed-call")
    assert r.status_code == 409 and r.json()["code"] == "agent_unavailable"
    with session_factory() as s:
        s.execute(update(Agent).where(Agent.name == "ticket-triage").values(version=1, active=False))
        s.commit()
    assert call(ext, secret, "POST", "/runs", input="x", request_id="changed-call").status_code == 409


def test_an_input_that_is_too_big_or_empty_is_a_422(ext, setup):
    _, _, secret = setup
    assert call(ext, secret, "POST", "/runs", input="x" * 4001, request_id="big-call-01").status_code == 422
    assert call(ext, secret, "POST", "/runs", input="", request_id="empty-call1").status_code == 422
    assert call(ext, secret, "POST", "/runs", input="ok", request_id="short").status_code == 422


# ---------- isolation ----------


def test_one_key_cannot_read_or_touch_anothers_run(client, session_factory, ext, setup):
    project, _, secret = setup
    other = make_key(client, project, name="second")
    run_id = call(ext, secret, "POST", "/runs", input="x", request_id="mine-call-01").json()["run_id"]
    for method, path in [("GET", f"/runs/{run_id}"), ("POST", f"/runs/{run_id}/cancel")]:
        assert call(ext, other["secret"], method, path).status_code == 404
    assert call(ext, other["secret"], "POST", f"/runs/{run_id}/answer", text="x").status_code == 404
    assert call(ext, secret, "GET", f"/runs/{run_id}").status_code == 200
    assert call(ext, secret, "GET", "/runs/99999").status_code == 404


# ---------- the surface ----------


def test_the_external_api_has_no_way_to_approve_anything():
    paths = {(m.upper(), p) for p, ops in app.openapi()["paths"].items() if p.startswith("/api/v1") for m in ops}
    assert paths == {("GET", "/api/v1/me"), ("POST", "/api/v1/runs"), ("GET", "/api/v1/runs/{run_id}"),
                     ("POST", "/api/v1/runs/{run_id}/answer"), ("POST", "/api/v1/runs/{run_id}/cancel")}
    assert not any(word in p for _, p in paths for word in ("approve", "decide", "respond", "activate", "unblock"))


# ---------- review fixes ----------


def test_the_budget_per_run_is_enforced_before_the_model_is_called(client, session_factory, ext, setup):
    project, _, _ = setup
    tiny = make_key(client, project, budget_per_run_usd=0.0001)
    run_id = call(ext, tiny["secret"], "POST", "/runs", input="x", request_id="budget-call-1").json()["run_id"]
    fake = step(session_factory, [FINISH])
    seen = call(ext, tiny["secret"], "GET", f"/runs/{run_id}").json()
    assert (seen["state"], seen["reason"]) == ("stopped", "budget_exhausted") and fake.requests == []


def test_revoking_a_key_stops_the_runs_it_left_open(client, session_factory, ext, setup):
    project, key, secret = setup
    open_run = call(ext, secret, "POST", "/runs", input="x", request_id="revoke-call1").json()["run_id"]
    waiting = call(ext, secret, "POST", "/runs", input="y", request_id="revoke-call2").json()["run_id"]
    step(session_factory, [ASK])
    assert client.delete(f"/api-keys/{key['id']}").status_code == 200
    with session_factory() as s:
        states = {r.id: (r.state, r.reason) for r in s.scalars(select(Run))}
    assert states[open_run] == ("stopped", "run_cancelled") and states[waiting] == ("stopped", "run_cancelled")
    tasks = client.get(f"/projects/{project['id']}/tasks").json()
    assert {t["status"] for t in tasks} == {"FAILED"}


def test_the_same_request_id_with_another_input_is_not_a_replay(ext, setup):
    _, _, secret = setup
    assert call(ext, secret, "POST", "/runs", input="first", request_id="reuse-call-01").status_code == 201
    r = call(ext, secret, "POST", "/runs", input="second", request_id="reuse-call-01")
    assert r.status_code == 409 and r.json()["code"] == "api_request_reused"


def test_keys_and_calls_leave_events_in_the_project(client, session_factory, ext, setup):
    project, key, secret = setup
    run_id = call(ext, secret, "POST", "/runs", input="x", request_id="event-call-01").json()["run_id"]
    step(session_factory, [ASK])
    call(ext, secret, "POST", f"/runs/{run_id}/answer", text="March")
    client.delete(f"/api-keys/{key['id']}")
    types = [e["type"] for e in client.get(f"/projects/{project['id']}/events").json()]
    for expected in ("api_key.created", "api.run_started", "api.run_answered", "api_key.revoked"):
        assert expected in types, expected
    events = json.dumps(client.get(f"/projects/{project['id']}/events").json())
    assert secret not in events  # never the secret, only the prefix


def test_the_list_counts_each_keys_runs_in_one_query(client, session_factory, ext, setup):
    project, key, secret = setup
    other = make_key(client, project, name="other")
    call(ext, secret, "POST", "/runs", input="x", request_id="count-call-01")
    call(ext, secret, "POST", "/runs", input="y", request_id="count-call-02")
    used = {k["name"]: k["runs_today"] for k in client.get("/api-keys").json()["items"]}
    assert used == {"CI": 2, "other": 0} and other["id"] != key["id"]
