"""Night queue item 6: the project's own `mode` (chosen at creation) and a task's `mode` (when set)
feed the resolved settings, so an automatic project auto-decides and a manual_learning one waits for
the owner. Precedence: app/settings_layers.py::resolve. FakeProvider only, no real model calls.
"""

import pytest

from app.main import app
from app.models import Mode, Project, SettingsLayer, SettingsScope, Task, User, Workspace
from app.routers.manager import get_gateway
from app.settings_layers import resolve
from tests.test_manager import CHECKPOINT, GOOD_PLAN, _ready_task, register_researcher, use_fake
from tests.test_task_run import RUN_RESULT, use_fake_role


def _project(client, mode):
    user = client.post("/users", json={"email": f"{mode}@example.com"}).json()
    return client.post("/projects", json={"owner_id": user["id"], "title": "P", "goal": "g", "mode": mode}).json()


# ---------- end to end: checkpoint, plan prompt, learning trace ----------


def test_automatic_project_auto_decides_with_no_settings_layer(client, session_factory):
    pid = _project(client, "automatic")["id"]
    t = _ready_task(client, pid, risk="low")
    use_fake(client, session_factory, [CHECKPOINT])
    assert client.get(f"/settings/project/{pid}").json()["values"] == {}  # only the creation field says so

    r = client.post(f"/projects/{pid}/tasks/{t['id']}/checkpoint")
    assert r.status_code == 200, r.text
    assert r.json()["auto_decided"] is True
    assert client.get(f"/projects/{pid}/tasks/{t['id']}").json()["status"] == "RUNNING"
    step = next(e for e in client.get(f"/projects/{pid}/events").json() if e["type"] == "decision.step")
    assert step["payload"]["auto"] is True and step["payload"]["option"] == CHECKPOINT["recommended"]
    assert client.get("/inbox").json() == []
    app.dependency_overrides.pop(get_gateway, None)


def test_manual_learning_project_waits_for_the_owner(client, session_factory):
    pid = _project(client, "manual_learning")["id"]
    t = _ready_task(client, pid, risk="low")
    use_fake(client, session_factory, [CHECKPOINT])

    r = client.post(f"/projects/{pid}/tasks/{t['id']}/checkpoint")
    assert r.status_code == 200, r.text
    assert r.json()["auto_decided"] is False
    assert client.get(f"/projects/{pid}/tasks/{t['id']}").json()["status"] == "READY"
    assert not any(e["type"] == "decision.step" for e in client.get(f"/projects/{pid}/events").json())
    assert [i["task_id"] for i in client.get("/inbox").json()] == [t["id"]]
    app.dependency_overrides.pop(get_gateway, None)


def test_automatic_project_still_waits_on_high_risk(client, session_factory):
    """The mode must not bypass approvals (ARCHITECTURE §6, §36): high risk maps to "user"."""
    pid = _project(client, "automatic")["id"]
    t = _ready_task(client, pid, risk="high")
    use_fake(client, session_factory, [CHECKPOINT])
    assert client.post(f"/projects/{pid}/tasks/{t['id']}/checkpoint").json()["auto_decided"] is False
    assert client.get(f"/projects/{pid}/tasks/{t['id']}").json()["status"] == "READY"
    app.dependency_overrides.pop(get_gateway, None)


@pytest.mark.parametrize("project_mode,task_mode,auto", [
    ("manual_learning", "automatic", True),
    ("automatic", "manual_learning", False),
])
def test_task_mode_overrides_the_projects(client, session_factory, project_mode, task_mode, auto):
    pid = _project(client, project_mode)["id"]
    t = _ready_task(client, pid, risk="low", mode=task_mode)
    use_fake(client, session_factory, [CHECKPOINT])
    assert client.post(f"/projects/{pid}/tasks/{t['id']}/checkpoint").json()["auto_decided"] is auto
    app.dependency_overrides.pop(get_gateway, None)


@pytest.mark.parametrize("mode", ["automatic", "manual_learning"])
def test_plan_prompt_states_the_projects_own_mode(client, session_factory, mode):
    """The real-run symptom: an automatic project's plan prompt said "Mode: manual_learning"."""
    pid = _project(client, mode)["id"]
    register_researcher(client)
    fake = use_fake(client, session_factory, [GOOD_PLAN])
    assert client.post(f"/projects/{pid}/plan").status_code == 200
    assert f"Mode: {mode}\n" in fake.requests[0].user
    app.dependency_overrides.pop(get_gateway, None)


@pytest.mark.parametrize("mode,traces", [("manual_learning", 1), ("automatic", 0)])
def test_learning_trace_follows_the_projects_own_mode(client, session_factory, mode, traces):
    pid = _project(client, mode)["id"]
    t = client.post(f"/projects/{pid}/tasks", json={"title": "T", "owner": "researcher"}).json()
    for status in ("READY", "RUNNING"):
        client.post(f"/projects/{pid}/tasks/{t['id']}/transition", json={"status": status})
    use_fake_role(client, session_factory, "research", [RUN_RESULT])
    assert client.post(f"/projects/{pid}/tasks/{t['id']}/run").status_code == 200
    assert len(client.get(f"/projects/{pid}/learning").json()) == traces
    app.dependency_overrides.pop(get_gateway, None)


# ---------- precedence (resolve) ----------


def _seed(session, *, project_mode, task_mode=None):
    user, ws = User(email="u@example.com"), Workspace(name="W")
    session.add_all([user, ws])
    session.flush()
    project = Project(owner_id=user.id, workspace_id=ws.id, title="P", goal="g", mode=project_mode)
    session.add(project)
    session.flush()
    task = Task(project_id=project.id, title="T", mode=task_mode)
    session.add(task)
    session.flush()
    return ws, project, task


def _layer(session, scope, scope_id, values):
    session.add(SettingsLayer(scope=scope, scope_id=scope_id, values=values))
    session.flush()


def test_project_mode_beats_global_and_workspace_layers(session):
    ws, project, task = _seed(session, project_mode=Mode.AUTOMATIC)
    _layer(session, SettingsScope.GLOBAL, 0, {"mode": "manual_learning"})
    _layer(session, SettingsScope.WORKSPACE, ws.id, {"mode": "manual_learning"})
    assert resolve(session, project_id=project.id)["mode"] == "automatic"
    assert resolve(session, task_id=task.id)["mode"] == "automatic"  # task.mode None: inherits, project found via task
    assert resolve(session, workspace_id=ws.id)["mode"] == "manual_learning"  # no project in scope


def test_project_layer_overrides_the_projects_own_mode(session):
    _, project, task = _seed(session, project_mode=Mode.AUTOMATIC)
    _layer(session, SettingsScope.PROJECT, project.id, {"mode": "manual_learning"})
    assert resolve(session, project_id=project.id)["mode"] == "manual_learning"
    assert resolve(session, task_id=task.id)["mode"] == "manual_learning"


def test_task_mode_beats_project_mode_and_layer_but_not_the_task_layer(session):
    _, project, task = _seed(session, project_mode=Mode.MANUAL_LEARNING, task_mode=Mode.AUTOMATIC)
    _layer(session, SettingsScope.PROJECT, project.id, {"mode": "manual_learning"})
    assert resolve(session, task_id=task.id)["mode"] == "automatic"
    assert resolve(session, project_id=project.id, task_id=task.id)["mode"] == "automatic"  # Gateway.call's form
    assert resolve(session, project_id=project.id)["mode"] == "manual_learning"  # a task's mode stays task-scoped
    _layer(session, SettingsScope.TASK, task.id, {"mode": "manual_learning"})
    assert resolve(session, task_id=task.id)["mode"] == "manual_learning"
