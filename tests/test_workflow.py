"""Workflows (app/workflow.py, issue #2 PR6, owner decision d40): agents connected by typed edges, versioned, checked in
code before they are accepted, approved as exactly one version, run as the project's tasks through the manager's plan.
Nothing here makes a model call except the one test that completes a task by hand."""

import pytest

from app import workflow
from app.models import Agent, Task, WorkflowVersion
from tests.test_factory_build import act, passed
from tests.test_factory_intake import rid

RESEARCHER = {"agent": "researcher", "version": 2}  # registry file agent: tools ["web_search"], network on


def agent_node(node_id, **kw):
    return {"id": node_id, "type": "agent", **kw}


def data_edge(edge_id, src, dst, mapping=None):
    return {"id": edge_id, "kind": "data", "from": {"node": src, "port": "result"}, "to": {"node": dst, "port": "input"},
            "mapping": mapping if mapping is not None else [{"source": "summary", "target": "task"}]}


def two_agents():
    return {"nodes": [agent_node("a", **RESEARCHER, task="Find the pains"),
                      agent_node("b", agent="product", version=1)],
            "edges": [data_edge("e1", "a", "b")]}


def new_workflow(client, name="Pipeline"):
    r = client.post("/workflows", json={"name": name, "request_id": rid()})
    assert r.status_code == 201, r.text
    return r.json()


def save(client, w, graph, **kw):
    return client.put(f"/workflows/{w['id']}/draft", json={
        **graph, "expected_revision": w["revision"], "request_id": kw.pop("request_id", None) or rid()})


def codes(w):
    return sorted(i["code"] for i in w["issues"])


def valid(client):
    w = new_workflow(client)
    r = save(client, w, two_agents())
    assert r.status_code == 200 and r.json()["issues"] == [], r.text
    return r.json()


def approve(client, w, **kw):
    return client.post(f"/workflows/{w['id']}/approve", json={
        "version": w["version"], "expected_revision": w["revision"], "request_id": kw.pop("request_id", None) or rid()})


def run(client, w, project_id, **kw):
    return client.post(f"/workflows/{w['id']}/run", json={
        "version": kw.pop("version", w["version"]), "project_id": project_id, "request_id": kw.pop("request_id", None) or rid()})


# ---------- creating and saving ----------


def test_a_new_workflow_is_empty_and_says_what_is_missing(client):
    w = new_workflow(client)
    assert (w["version"], w["revision"], w["nodes"], w["approved_version"]) == (1, 1, [], None)
    assert codes(w) == ["no_agent"] and not w["can_approve"]
    names = {(a["name"], a["version"]) for a in w["catalog"]["agents"]}
    assert ("researcher", 2) in names and ("manager", 1) not in {n for n in names if n[0] == "nobody"}
    assert w["catalog"]["implemented_tools"] == ["memory_search"] and "web_search" in w["catalog"]["tools"]
    assert w["catalog"]["output_fields"]["findings"] == "list"


def test_a_valid_graph_saves_a_version_and_can_be_approved(client):
    w = valid(client)
    assert w["version"] == 2 and w["revision"] == 2 and w["can_approve"] and not w["approved"]
    r = approve(client, w)
    assert r.status_code == 200, r.text
    done = r.json()
    assert done["approved"] and done["approved_version"] == 2 and done["issues"] == [] and done["revision"] == 3


def test_saving_the_same_graph_again_changes_nothing(client, session_factory):
    w = valid(client)
    again = save(client, w, two_agents()).json()
    assert (again["version"], again["revision"]) == (2, 2)
    with session_factory() as s:
        assert s.query(WorkflowVersion).count() == 2


def test_moving_a_node_is_not_a_change(client):
    w = valid(client)
    w = approve(client, w).json()
    r = client.put(f"/workflows/{w['id']}/layout", json={"positions": {"a": {"x": 10.5, "y": 20}, "b": {"x": 300, "y": 20}}})
    assert r.status_code == 200
    moved = r.json()
    assert moved["layout"]["b"]["x"] == 300
    assert (moved["version"], moved["revision"], moved["approved"], moved["approved_version"]) == (
        w["version"], w["revision"], True, w["approved_version"])


def test_a_stale_revision_and_a_replayed_request(client, session_factory):
    w = valid(client)
    r = client.put(f"/workflows/{w['id']}/draft", json={**two_agents(), "expected_revision": 1, "request_id": rid()})
    assert r.status_code == 409 and r.json()["code"] == "stale_revision" and r.json()["params"]["current_revision"] == 2
    same = rid()
    graph = {"nodes": [agent_node("a", **RESEARCHER, task="Another task")], "edges": []}
    first = save(client, w, graph, request_id=same)
    second = save(client, w, graph, request_id=same)
    assert first.status_code == second.status_code == 200 and second.json()["duplicate"] is True
    with session_factory() as s:
        assert s.query(WorkflowVersion).count() == 3


def test_a_new_draft_is_not_approved_and_the_old_approval_stays_with_its_version(client):
    w = approve(client, valid(client)).json()
    graph = two_agents()
    graph["nodes"][0]["task"] = "A different first task"
    w2 = save(client, w, graph).json()
    assert w2["version"] == 3 and not w2["approved"] and w2["approved_version"] == 2
    r = approve(client, {**w2, "version": 2})
    assert r.status_code == 409 and r.json()["code"] == "stale_version"  # only the latest can be approved


def test_an_invalid_graph_cannot_be_approved(client):
    w = new_workflow(client)
    w = save(client, w, {"nodes": [agent_node("a", **RESEARCHER)], "edges": []}).json()  # no task, nothing mapped
    assert codes(w) == ["required_input_missing"]
    r = approve(client, w)
    assert r.status_code == 409 and r.json()["code"] == "workflow_invalid"
    assert r.json()["params"]["issues"][0]["code"] == "required_input_missing"


# ---------- the validator: each reason, in code ----------


def test_a_data_edge_needs_a_mapping_and_fields_that_exist(client):
    w = new_workflow(client)
    graph = two_agents()
    graph["edges"] = [data_edge("e1", "a", "b", mapping=[])]
    assert "mapping_missing" in codes(save(client, w, graph).json())
    w = client.get(f"/workflows/{w['id']}").json()
    graph["edges"] = [data_edge("e1", "a", "b", mapping=[{"source": "nope", "target": "task"}])]
    assert "mapping_unknown_field" in codes(save(client, w, graph).json())


def test_a_type_mismatch_is_refused_with_the_explicit_conversion_as_the_suggestion(client):
    w = new_workflow(client)
    graph = two_agents()
    graph["edges"] = [data_edge("e1", "a", "b", mapping=[{"source": "findings", "target": "task"}])]
    saved = save(client, w, graph).json()
    [issue] = [i for i in saved["issues"] if i["code"] == "mapping_type_mismatch"]
    assert (issue["have"], issue["want"], issue["suggest"]) == ("list", "text", "join")
    graph["edges"] = [data_edge("e1", "a", "b", mapping=[{"source": "findings", "target": "task", "convert": "join"}])]
    assert save(client, saved, graph).json()["issues"] == []  # only because it was said out loud
    graph["edges"] = [data_edge("e1", "a", "b", mapping=[{"source": "summary", "target": "task", "convert": "join"}])]
    again = client.get(f"/workflows/{w['id']}").json()
    assert "mapping_type_mismatch" in codes(save(client, again, graph).json())  # a text can't be "joined"


def test_two_mappings_into_one_field_and_a_missing_required_input(client):
    w = new_workflow(client)
    graph = {"nodes": [agent_node("a", **RESEARCHER, task="x"), agent_node("c", **RESEARCHER, task="y"),
                       agent_node("b", agent="product", version=1)],
             "edges": [data_edge("e1", "a", "b"), data_edge("e2", "c", "b")]}
    assert "mapping_target_twice" in codes(save(client, w, graph).json())
    w = client.get(f"/workflows/{w['id']}").json()
    graph["edges"] = [data_edge("e1", "a", "b", mapping=[{"source": "summary", "target": "context"}])]
    got = codes(save(client, w, graph).json())
    assert "required_input_missing" in got


def test_a_cycle_a_self_edge_and_wrong_ports_are_refused(client):
    w = new_workflow(client)
    graph = {"nodes": [agent_node("a", **RESEARCHER, task="x"), agent_node("b", agent="product", version=1, task="y")],
             "edges": [data_edge("e1", "a", "b"), data_edge("e2", "b", "a")]}
    saved = save(client, w, graph).json()
    [cycle] = [i for i in saved["issues"] if i["code"] == "cycle"]
    assert cycle["nodes"] == ["a", "b"]
    graph["edges"] = [data_edge("e1", "a", "a")]
    assert "edge_self" in codes(save(client, saved, graph).json())
    saved = client.get(f"/workflows/{w['id']}").json()
    graph["edges"] = [{"id": "e1", "kind": "data", "from": {"node": "a", "port": "input"},
                       "to": {"node": "b", "port": "input"}, "mapping": [{"source": "summary", "target": "task"}]}]
    assert "edge_wrong_ports" in codes(save(client, saved, graph).json())
    saved = client.get(f"/workflows/{w['id']}").json()
    graph["edges"] = [data_edge("e1", "a", "ghost")]
    assert "edge_unknown_node" in codes(save(client, saved, graph).json())


def test_an_agent_that_is_not_there_or_not_at_that_version_cannot_be_pinned(client):
    w = new_workflow(client)
    graph = {"nodes": [agent_node("a", agent="researcher", version=1, task="x"),
                       agent_node("b", agent="nobody", version=1, task="y")], "edges": []}
    saved = save(client, w, graph).json()
    assert [i["code"] for i in saved["issues"]].count("unknown_agent") == 2


def test_tool_edges_need_the_tool_its_permission_and_an_implementation(client):
    client.post("/agents", json={"name": "runner", "description": "d", "model_role": "research",
                                 "capabilities": ["wf-tests"], "tools": ["memory_search"]})
    w = new_workflow(client)

    def tool_graph(agent, version, tool):
        return {"nodes": [agent_node("a", agent=agent, version=version, task="x"), {"id": "t", "type": "tool", "tool": tool}],
                "edges": [{"id": "e1", "kind": "tool", "from": {"node": "t", "port": "out"},
                           "to": {"node": "a", "port": "tools"}}]}

    ok = save(client, w, tool_graph("runner", 1, "memory_search")).json()
    assert ok["issues"] == []
    got = codes(save(client, ok, tool_graph("runner", 1, "deploy")).json())
    assert got == ["tool_unknown"]  # the agent doesn't have it
    got = codes(save(client, client.get(f"/workflows/{w['id']}").json(), tool_graph("researcher", 2, "web_search")).json())
    assert got == ["tool_unavailable"]  # it has it and may use it, but no Tool Gateway implements it yet


def test_knowledge_and_model_edges(client):
    w = new_workflow(client)
    graph = {"nodes": [agent_node("a", **RESEARCHER, task="x"), {"id": "s", "type": "source", "source": "task.input"},
                       {"id": "m", "type": "model", "role": "cheap"}],
             "edges": [{"id": "e1", "kind": "knowledge", "from": {"node": "s", "port": "out"},
                        "to": {"node": "a", "port": "knowledge"}},
                       {"id": "e2", "kind": "model", "from": {"node": "m", "port": "out"},
                        "to": {"node": "a", "port": "model"}}]}
    ok = save(client, w, graph).json()
    assert ok["issues"] == []
    graph["nodes"][1]["source"] = "the_internet"
    graph["nodes"][2]["role"] = "wizard"
    graph["nodes"].append({"id": "m2", "type": "model", "role": "cheap"})
    graph["edges"].append({"id": "e3", "kind": "model", "from": {"node": "m2", "port": "out"},
                           "to": {"node": "a", "port": "model"}})
    got = codes(save(client, ok, graph).json())
    assert {"knowledge_not_allowed", "model_role_invalid", "model_twice"} <= set(got)


def test_a_node_with_no_connection_is_flagged(client):
    w = new_workflow(client)
    graph = {"nodes": [agent_node("a", **RESEARCHER, task="x"), {"id": "t", "type": "tool", "tool": "memory_search"}],
             "edges": []}
    assert codes(save(client, w, graph).json()) == ["unlinked_node"]


def test_a_request_body_with_the_wrong_shape_is_a_422(client):
    w = new_workflow(client)
    bad = {"nodes": [{"id": "a", "type": "wizard"}], "edges": [], "expected_revision": 1, "request_id": rid()}
    assert client.put(f"/workflows/{w['id']}/draft", json=bad).status_code == 422
    huge = {"nodes": [agent_node(f"n{i}", **RESEARCHER) for i in range(workflow.MAX_NODES + 1)], "edges": [],
            "expected_revision": 1, "request_id": rid()}
    assert client.put(f"/workflows/{w['id']}/draft", json=huge).status_code == 422


# ---------- a built agent ----------


def test_only_a_version_that_passed_and_is_yours_can_be_pinned(client, session_factory):
    st = passed(client, session_factory)  # ticket-triage v1: passed, not yet active
    w = new_workflow(client)
    graph = {"nodes": [agent_node("a", agent="ticket-triage", version=1, task="x")], "edges": []}
    assert save(client, w, graph).json()["issues"] == []
    assert ("ticket-triage", 1) in {(a["name"], a["version"]) for a in client.get(f"/workflows/{w['id']}").json()["catalog"]["agents"]}
    client.post("/users", json={"email": "other@example.com"})  # the client now acts as another account
    other = new_workflow(client, "Mine")
    assert codes(save(client, other, graph).json()) == ["unknown_agent"]
    assert "ticket-triage" not in {a["name"] for a in other["catalog"]["agents"]}
    assert act(client, st, "activate", agent_version=1).status_code == 404  # and the build is not theirs


# ---------- run ----------


def make_project(client, owner_id):
    return client.post("/projects", json={"owner_id": owner_id, "title": "P", "goal": "A goal"}).json()


def test_run_needs_an_approved_version_and_makes_the_projects_tasks(client, session_factory):
    w = valid(client)
    me = client.get("/auth/me").json()
    project = make_project(client, me["id"])
    r = run(client, w, project["id"])
    assert r.status_code == 409 and r.json()["code"] == "workflow_not_approved"
    w = approve(client, w).json()
    r = run(client, w, project["id"])
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["version"] == 2 and len(body["task_ids"]) == 2 and body["specialists_requested"] == []

    tasks = {t["owner"]: t for t in client.get(f"/projects/{project['id']}/tasks").json()}
    first, second = tasks["researcher"], tasks["product"]
    assert first["status"] == second["status"] == "CREATED" and second["depends_on"] == [first["id"]]
    events = [e["type"] for e in client.get(f"/projects/{project['id']}/events").json()]
    assert "workflow.run" in events and events.index("plan.proposed") < events.index("approval.requested")
    with session_factory() as s:
        t2 = s.get(Task, second["id"])
        assert t2.input["workflow"] == {"id": w["id"], "version": 2, "node": "b", "agent_version": 1}
        assert t2.input["mappings"] == [{"from_task": first["id"], "source": "summary", "target": "task", "convert": None}]
    # the plan still waits for the owner: only approving it moves the first task
    assert client.post(f"/projects/{project['id']}/plan/approve").json()["ready_task_ids"] == [first["id"]]


def test_a_mapped_input_reaches_the_next_agents_context(client, session_factory):
    from app import context

    w = new_workflow(client)
    graph = two_agents()
    graph["edges"] = [data_edge("e1", "a", "b", mapping=[{"source": "findings", "target": "task", "convert": "join"}])]
    w = approve(client, save(client, w, graph).json()).json()
    project = make_project(client, client.get("/auth/me").json()["id"])
    run(client, w, project["id"])
    tasks = {t["owner"]: t for t in client.get(f"/projects/{project['id']}/tasks").json()}
    with session_factory() as s:
        s.get(Task, tasks["researcher"]["id"]).output = {
            "summary": "s", "lesson": "l", "next": "n",
            "findings": [{"claim": "Phone booking eats mornings", "type": "FACT", "basis": "b"}, {"claim": "Second claim", "type": "INFERENCE", "basis": "b"}]}
        s.commit()
        text = context.task_context(s, s.get(Task, tasks["product"]["id"]))
    assert "Inputs mapped from earlier tasks" in text and "task: Phone booking eats mornings\nSecond claim" in text


def test_a_missing_agent_blocks_its_task_and_a_changed_version_refuses(client, session_factory):
    w = approve(client, valid(client)).json()
    project = make_project(client, client.get("/auth/me").json()["id"])
    with session_factory() as s:
        s.query(Agent).filter_by(name="product").update({"active": False})
        s.commit()
    r = run(client, w, project["id"])
    assert r.status_code == 201 and r.json()["specialists_requested"] == ["product"]
    tasks = {t["owner"]: t for t in client.get(f"/projects/{project['id']}/tasks").json()}
    assert (tasks["product"]["status"], tasks["product"]["reason"]) == ("BLOCKED", "missing_specialist")
    assert tasks["product"]["depends_on"] == [tasks["researcher"]["id"]]  # the edge is kept

    with session_factory() as s:
        s.query(Agent).filter_by(name="product").update({"active": True, "version": 9})
        s.commit()
    r = run(client, w, project["id"])
    assert r.status_code == 409 and r.json()["code"] == "agent_version_changed"
    assert r.json()["params"]["pinned"] == 1 and r.json()["params"]["active"] == 9


def test_a_replayed_run_makes_the_tasks_once(client):
    w = approve(client, valid(client)).json()
    project = make_project(client, client.get("/auth/me").json()["id"])
    same = rid()
    first = run(client, w, project["id"], request_id=same)
    second = run(client, w, project["id"], request_id=same)
    assert first.status_code == second.status_code == 201 and second.json() == {"duplicate": True}
    assert len(client.get(f"/projects/{project['id']}/tasks").json()) == 2


def test_a_paused_project_refuses_the_run(client):
    w = approve(client, valid(client)).json()
    project = make_project(client, client.get("/auth/me").json()["id"])
    client.post(f"/projects/{project['id']}/pause", json={"paused": True})
    r = run(client, w, project["id"])
    assert r.status_code == 409 and r.json()["code"] == "project_paused"
    assert client.get(f"/projects/{project['id']}/tasks").json() == []


# ---------- isolation ----------


def test_another_account_sees_and_touches_nothing(client):
    w = approve(client, valid(client)).json()
    mine = make_project(client, client.get("/auth/me").json()["id"])
    client.post("/users", json={"email": "intruder@example.com"})
    assert client.get("/workflows").json() == []
    for method, path, body in [
        ("GET", f"/workflows/{w['id']}", None),
        ("PUT", f"/workflows/{w['id']}/layout", {"positions": {}}),
        ("PUT", f"/workflows/{w['id']}/draft", {**two_agents(), "expected_revision": 1, "request_id": rid()}),
        ("POST", f"/workflows/{w['id']}/approve", {"version": 3, "expected_revision": 3, "request_id": rid()}),
        ("POST", f"/workflows/{w['id']}/run", {"version": 3, "project_id": mine["id"], "request_id": rid()}),
    ]:
        r = client.request(method, path, json=body) if body is not None else client.request(method, path)
        assert r.status_code == 404, (path, r.status_code)
    other = new_workflow(client, "Theirs")
    r = run(client, {**w, "id": other["id"]}, mine["id"])  # their own workflow into my project
    assert r.status_code == 404


def test_the_closed_sets_are_what_the_code_writes():
    assert workflow.ISSUE_CODES >= {"cycle", "mapping_type_mismatch", "tool_not_granted"}
    assert set(workflow.PORTS) == set(workflow.EDGE_KINDS)


# ---------- review fixes ----------


def test_a_mapping_can_only_read_a_dependency_of_its_own_project(client, session_factory):
    """task.input is the owner's to write: a planted mapping must not pull another account's task output."""
    from app import context

    victim = client.post("/users", json={"email": "victim@example.com"}).json()
    mine = make_project(client, victim["id"])
    r1 = client.post(f"/projects/{mine['id']}/tasks", json={"title": "A", "owner": "researcher"})
    assert r1.status_code == 201, (mine, r1.text)
    t1 = r1.json()
    with session_factory() as s:
        s.get(Task, t1["id"]).output = {"summary": "SECRET of the first account", "lesson": "l", "next": "n", "findings": []}
        s.commit()
    attacker = client.post("/users", json={"email": "attacker@example.com"}).json()
    theirs = make_project(client, attacker["id"])
    planted = {"mappings": [{"from_task": t1["id"], "source": "summary", "target": "task", "convert": None}]}
    t2 = client.post(f"/projects/{theirs['id']}/tasks", json={"title": "B", "owner": "product", "input": planted}).json()
    with session_factory() as s:
        assert "SECRET" not in context.task_context(s, s.get(Task, t2["id"]))  # not a dependency, not their project


def test_a_replayed_create_returns_its_own_workflow(client):
    same = rid()
    first = client.post("/workflows", json={"name": "One", "request_id": same}).json()
    other = client.post("/workflows", json={"name": "Two", "request_id": rid()}).json()
    again = client.post("/workflows", json={"name": "One", "request_id": same}).json()
    assert again["id"] == first["id"] and again["duplicate"] is True and other["id"] != first["id"]
    client.post("/factory/sessions", json={"request_id": "factoryreq1"})
    fresh = client.post("/workflows", json={"name": "Three", "request_id": "factoryreq1"})
    assert fresh.status_code == 201 and fresh.json()["duplicate"] is False


def test_the_layout_is_bounded_and_only_for_the_workflows_nodes(client):
    w = valid(client)
    bad = [{"a": {"x": 1e308, "y": 0}}, {"a": {"x": 1, "y": 2, "z": 3}}, {"a" * 41: {"x": 1, "y": 2}}]
    for positions in bad:
        assert client.put(f"/workflows/{w['id']}/layout", json={"positions": positions}).status_code == 422
    r = client.put(f"/workflows/{w['id']}/layout", json={"positions": {"a": {"x": 5, "y": 6}, "ghost": {"x": 1, "y": 1}}})
    assert r.json()["layout"] == {"a": {"x": 5.0, "y": 6.0}}


def test_a_changed_registry_agent_under_the_same_version_refuses_the_run(client, session_factory):
    w = approve(client, valid(client)).json()
    project = make_project(client, client.get("/auth/me").json()["id"])
    with session_factory() as s:
        s.query(Agent).filter_by(name="product").update({"instructions": "Now it does something else."})
        s.commit()
    r = run(client, w, project["id"])
    assert r.status_code == 409 and r.json()["code"] == "agent_version_changed"


def test_a_malformed_edge_id_has_its_own_message(client):
    w = new_workflow(client)
    graph = two_agents()
    graph["edges"] = [{**data_edge("E 1", "a", "b")}]
    assert "bad_edge_id" in codes(save(client, w, graph).json())


def test_the_list_shows_every_workflow_with_its_approval(client):
    a = approve(client, valid(client)).json()
    new_workflow(client, "Empty one")
    rows = {r["name"]: r for r in client.get("/workflows").json()}
    assert rows["Pipeline"]["approved_version"] == a["version"] and rows["Pipeline"]["agents"] == 2
    assert rows["Empty one"]["approved_version"] is None and rows["Empty one"]["agents"] == 0
