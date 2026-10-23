"""What an independent security read of the whole stack found (PR8), each pinned by a test: an outside caller's run is
walled off from the owner's project memory; an agent one account built is not another account's; behind a proxy there
is no "local machine" and the cookie is Secure."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app import context, registry
from app.db import get_session
from app.main import app
from app.models import Agent, MemoryCategory, MemoryItem, Run, Task, User
from tests.test_external_api import FINISH, activated, bearer, call, make_key, step
from tests.test_factory_build import fake_models
from tests.test_auth import _legacy


# ---------- 1. an outside caller's run is walled off from the owner's memory ----------


def test_an_api_run_has_no_project_memory_and_writes_none(client, session_factory):
    project = activated(client, session_factory)
    fake_models(client, session_factory, [])
    client.post(f"/projects/{project['id']}/memory", json={
        "category": "brief", "title": "pricing", "content": "THE OWNERS PRIVATE PRICING NOTE"})
    with session_factory() as s:
        s.execute(update(Agent).where(Agent.name == "ticket-triage").values(tools=["memory_search"]))
        s.commit()
    key = make_key(client, project)
    ext = TestClient(app)
    run_id = call(ext, key["secret"], "POST", "/runs", input="what is the pricing?", request_id="wall-call-01").json()["run_id"]
    fake = step(session_factory, [FINISH])
    assert "THE OWNERS PRIVATE PRICING NOTE" not in fake.requests[0].user  # not in the prompt
    assert "memory_search" not in fake.requests[0].user.split("Tools you may use:")[1].split("\n")[0].replace("none", "")
    with session_factory() as s:
        assert s.get(Run, run_id).tools == []  # the tool itself is not there
        assert s.query(MemoryItem).count() == 1  # the owner's note only: the result was not written into memory


def test_the_owners_own_run_still_sees_the_memory(client, session_factory):
    project = activated(client, session_factory)
    client.post(f"/projects/{project['id']}/memory", json={"category": "brief", "title": "t", "content": "A NOTE"})
    with session_factory() as s:
        task = Task(project_id=project["id"], title="T", owner="ticket-triage", input={"goal": "g"})
        s.add(task)
        s.commit()
        assert "A NOTE" in context.task_context(s, task)
        assert "A NOTE" not in context.task_context(s, task, isolated=True)


# ---------- 2. an agent built by one account is that account's ----------


def test_another_accounts_built_agent_is_invisible_unusable_and_unplannable(client, session_factory):
    mine = activated(client, session_factory)  # 'ticket-triage', built and activated by the first account
    assert "ticket-triage" in [a["name"] for a in client.get("/agents").json()]
    assert client.get("/agents/ticket-triage").status_code == 200

    other = client.post("/users", json={"email": "other@example.com"}).json()  # the client now acts as them
    names = [a["name"] for a in client.get("/agents").json()]
    assert "ticket-triage" not in names and "researcher" in names  # registry agents are everyone's
    assert client.get("/agents/ticket-triage").status_code == 404
    assert "ticket-triage" not in [a["name"] for a in client.get("/agents", params={"capability": "ticket-triage"}).json()]
    with session_factory() as s:
        assert "ticket-triage" not in context.agent_directory(s, other["id"])
        assert registry.agent_for(s, "ticket-triage", other["id"]) is None
        assert registry.agent_for(s, "researcher", other["id"]) is not None

    project = client.post("/projects", json={"owner_id": other["id"], "title": "P", "goal": "g"}).json()
    task = client.post(f"/projects/{project['id']}/tasks", json={"title": "T", "owner": "ticket-triage"}).json()
    for status in ("READY", "RUNNING"):
        client.post(f"/projects/{project['id']}/tasks/{task['id']}/transition", json={"status": status})
    r = client.post(f"/projects/{project['id']}/tasks/{task['id']}/runs")
    assert r.status_code == 409 and r.json()["code"] == "missing_specialist"  # their data never meets A's instructions
    r = client.post(f"/projects/{project['id']}/tasks/{task['id']}/run")
    assert r.status_code == 422  # the one-shot run: "agent 'ticket-triage' is not registered"


def test_a_workflow_cannot_pin_or_plan_with_anothers_agent(client, session_factory):
    activated(client, session_factory)
    other = client.post("/users", json={"email": "other@example.com"}).json()
    w = client.post("/workflows", json={"name": "W", "request_id": "wfstack00001"}).json()
    saved = client.put(f"/workflows/{w['id']}/draft", json={
        "nodes": [{"id": "a", "type": "agent", "agent": "ticket-triage", "version": 1, "task": "x"}], "edges": [],
        "expected_revision": w["revision"], "request_id": "wfstack00002"}).json()
    assert [i["code"] for i in saved["issues"]] == ["unknown_agent"] and other["id"]


# ---------- 3, 4. behind a proxy ----------


def test_behind_a_proxy_that_adds_no_header_a_claim_still_needs_the_owner_token(session_factory, monkeypatch):
    app.dependency_overrides[get_session] = lambda: session_factory().__enter__()
    try:
        uid = _legacy(session_factory)
        local = TestClient(app)
        monkeypatch.setenv("AGENT_FACTORY_BEHIND_PROXY", "1")
        r = local.post("/auth/claim", json={"user_id": uid})  # a loopback client, no forwarding header
        assert r.status_code == 403
        monkeypatch.setenv("AGENT_FACTORY_OWNER_TOKEN", "s3cret-token")
        assert local.post("/auth/claim", json={"user_id": uid, "owner_token": "s3cret-token"}).status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_the_cookie_is_secure_behind_a_proxy(session_factory, monkeypatch):
    def override():
        with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = override
    try:
        plain = TestClient(app).post("/users", json={"email": "a@example.com"})
        assert "secure" not in plain.headers["set-cookie"].lower()
        session_factory().close()
        with session_factory() as s:
            s.query(User).delete()
            s.commit()
        monkeypatch.setenv("AGENT_FACTORY_BEHIND_PROXY", "1")
        proxied = TestClient(app).post("/users", json={"email": "b@example.com"})
        assert "secure" in proxied.headers["set-cookie"].lower()
    finally:
        app.dependency_overrides.clear()
