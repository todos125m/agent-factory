"""interaction_policy and its ceiling (app/settings_layers.py::resolve, docs/HUMAN_GUIDED_FACTORY.md §4): every policy is
interactive, the legacy modes map onto them without granting anything, and a lower layer can never loosen a limit an
upper layer set. FakeProvider only."""

import pytest

from app.main import app
from app.models import SettingsLayer, SettingsScope
from app.routers.manager import get_gateway
from app.settings_layers import INTERACTION_POLICIES, MODE_POLICY, strictest
from tests.test_manager import CHECKPOINT, _ready_task, use_fake


def _project(client, mode, email=None):
    user = client.post("/users", json={"email": email or f"{mode}@example.com"}).json()
    return client.post("/projects", json={"owner_id": user["id"], "title": "P", "goal": "g", "mode": mode}).json()["id"]


def _resolved(client, **params):
    return client.get("/settings/resolved", params=params).json()


def test_strictest_orders_the_policies_and_fails_closed():
    assert strictest("delegated_bounded", "milestone") == "milestone"
    assert strictest("milestone", "step_by_step", "delegated_bounded") == "step_by_step"
    assert strictest("delegated_bounded") == "delegated_bounded"
    assert strictest("delegated_bounded", "bogus") == "step_by_step"
    assert strictest("delegated_bounded", {"x": 1}) == "step_by_step"


@pytest.mark.parametrize("mode", ["automatic", "manual_learning"])
def test_without_a_layer_the_policy_follows_the_mode(client, mode):
    pid = _project(client, mode)
    assert _resolved(client, project_id=pid)["interaction_policy"] == MODE_POLICY[mode]


def test_no_project_in_scope_resolves_the_default_mode_strictly(client):
    resolved = _resolved(client)
    assert resolved["mode"] == "manual_learning" and resolved["interaction_policy"] == "step_by_step"
    assert resolved["quality_target"] == "prototype" and resolved["output_locale"] == "auto"


def test_manual_learning_can_not_be_loosened_by_any_layer(client):
    """Mapping the legacy mode must not silently increase what the system may do: loosening is a mode change."""
    pid = _project(client, "manual_learning")
    client.put("/settings/global/0", json={"interaction_policy": "delegated_bounded"})
    client.put(f"/settings/project/{pid}", json={"interaction_policy": "delegated_bounded",
                                                  "max_interaction_policy": "delegated_bounded"})
    resolved = _resolved(client, project_id=pid)
    assert resolved["interaction_policy"] == "step_by_step"
    assert resolved["max_interaction_policy"] == "step_by_step"


def test_an_automatic_project_may_choose_delegated_bounded(client):
    pid = _project(client, "automatic")
    client.put(f"/settings/project/{pid}", json={"interaction_policy": "delegated_bounded"})
    assert _resolved(client, project_id=pid)["interaction_policy"] == "delegated_bounded"


def test_a_lower_layer_can_not_loosen_an_upper_ceiling(client, session_factory):
    pid = _project(client, "automatic")
    ws = client.post("/workspaces", json={"name": "W"}).json()["id"]
    with session_factory() as s:  # the project in the workspace
        from app.models import Project
        s.get(Project, pid).workspace_id = ws
        s.commit()
    client.put(f"/settings/workspace/{ws}", json={"max_interaction_policy": "milestone"})
    client.put(f"/settings/project/{pid}", json={"interaction_policy": "delegated_bounded",
                                                  "max_interaction_policy": "delegated_bounded"})
    resolved = _resolved(client, project_id=pid)
    assert resolved["max_interaction_policy"] == "milestone"
    assert resolved["interaction_policy"] == "milestone"

    t = _ready_task(client, pid, risk="low")
    client.put(f"/settings/task/{t['id']}", json={"max_interaction_policy": "step_by_step"})
    assert _resolved(client, task_id=t["id"])["interaction_policy"] == "step_by_step"  # tightening is fine
    assert _resolved(client, project_id=pid)["interaction_policy"] == "milestone"  # and stays task-scoped


def test_global_step_by_step_ceiling_stops_every_auto_decision(client, session_factory):
    pid = _project(client, "automatic")
    t = _ready_task(client, pid, risk="low")
    use_fake(client, session_factory, [CHECKPOINT])  # replaces the global layer: the ceiling goes in after it
    client.put("/settings/global/0", json={"models": {"manager": {"provider": "fake", "model": "claude-opus-5"}},
                                           "max_interaction_policy": "step_by_step"})
    r = client.post(f"/projects/{pid}/tasks/{t['id']}/checkpoint")
    assert r.status_code == 200, r.text
    assert r.json()["auto_decided"] is False
    assert client.get(f"/projects/{pid}/tasks/{t['id']}").json()["status"] == "READY"
    assert [i["task_id"] for i in client.get("/inbox").json()] == [t["id"]]
    app.dependency_overrides.pop(get_gateway, None)


def test_step_by_step_on_an_automatic_project_waits_for_the_owner(client, session_factory):
    pid = _project(client, "automatic")
    client.put(f"/settings/project/{pid}", json={"interaction_policy": "step_by_step"})
    t = _ready_task(client, pid, risk="low")
    use_fake(client, session_factory, [CHECKPOINT])
    assert client.post(f"/projects/{pid}/tasks/{t['id']}/checkpoint").json()["auto_decided"] is False
    app.dependency_overrides.pop(get_gateway, None)


@pytest.mark.parametrize("policy", ["milestone", "delegated_bounded"])
def test_automatic_project_keeps_its_auto_decisions_under_a_looser_policy(client, session_factory, policy):
    """Existing automatic projects behave exactly as before (milestone is what automatic maps to)."""
    pid = _project(client, "automatic")
    client.put(f"/settings/project/{pid}", json={"interaction_policy": policy})
    t = _ready_task(client, pid, risk="low")
    use_fake(client, session_factory, [CHECKPOINT])
    assert client.post(f"/projects/{pid}/tasks/{t['id']}/checkpoint").json()["auto_decided"] is True
    app.dependency_overrides.pop(get_gateway, None)


def test_no_policy_lets_a_high_risk_step_skip_the_owner(client, session_factory):
    pid = _project(client, "automatic")
    client.put(f"/settings/project/{pid}", json={"interaction_policy": "delegated_bounded"})
    t = _ready_task(client, pid, risk="high")
    use_fake(client, session_factory, [CHECKPOINT])
    assert client.post(f"/projects/{pid}/tasks/{t['id']}/checkpoint").json()["auto_decided"] is False
    app.dependency_overrides.pop(get_gateway, None)


@pytest.mark.parametrize("stored", ["bogus", 3, {"a": 1}])
def test_a_stored_unknown_ceiling_fails_closed(client, session_factory, stored):
    """A row written around PUT's validation (or by an older version) can't open anything up."""
    pid = _project(client, "automatic", email=f"x{abs(hash(str(stored)))}@example.com")
    with session_factory() as s:
        s.add(SettingsLayer(scope=SettingsScope.PROJECT, scope_id=pid, values={"max_interaction_policy": stored}))
        s.commit()
    assert _resolved(client, project_id=pid)["interaction_policy"] == "step_by_step"


@pytest.mark.parametrize("body", [
    {"interaction_policy": "autonomous"},
    {"max_interaction_policy": "free"},
    {"interaction_policy": None},
    {"quality_target": "production_ready"},  # never claimed: only a candidate until evidence says so
    {"output_locale": "de"},
])
def test_invalid_policy_values_are_refused_with_a_code(client, body):
    r = client.put("/settings/global/0", json=body)
    assert r.status_code == 422
    assert r.json()["code"] == "settings_invalid"


def test_every_policy_is_a_valid_layer_value(client):
    for policy in INTERACTION_POLICIES:
        assert client.put("/settings/global/0", json={"interaction_policy": policy,
                                                      "max_interaction_policy": policy}).status_code == 200


@pytest.mark.parametrize("stored_mode", [{"x": 1}, ["automatic"], 7])
def test_a_stored_non_string_mode_fails_closed_not_500(client, session_factory, stored_mode):
    pid = _project(client, "automatic", email=f"m{abs(hash(str(stored_mode)))}@example.com")
    with session_factory() as s:
        s.add(SettingsLayer(scope=SettingsScope.PROJECT, scope_id=pid, values={"mode": stored_mode}))
        s.commit()
    r = client.get("/settings/resolved", params={"project_id": pid})
    assert r.status_code == 200
    assert r.json()["interaction_policy"] == "step_by_step"
