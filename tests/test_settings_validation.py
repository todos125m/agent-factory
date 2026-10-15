"""PUT /settings/{scope}/{scope_id} validates values, not only top-level keys (app/schemas.py::
SettingsLayerIn). Before, e.g. {"mode": "auto"} on a project layer was stored and silently beat an
automatic project's own mode, and {"approvals": {"high": "usr"}} let a high-risk step auto-decide.
FakeProvider only, no real model calls.
"""

import pytest

from app.gateway.service import WEB_SEARCH_HARD_CEILING
from app.main import app
from app.models import Project, RiskLevel, SettingsLayer, SettingsScope
from app.registry import MODEL_ROLES
from app.routers.manager import get_gateway
from app.schemas import MAX_BUDGET_USD, ApprovalsLayer, BudgetLayer, ContextLayer, ModelRouteLayer, SettingsLayerIn
from app.settings_layers import DEFAULTS
from tests import postgres_limits
from tests.test_manager import CHECKPOINT, GOOD_PLAN, _ready_task, register_researcher, use_fake

REJECTED = [
    # mode / depth
    ({"mode": "auto"}, "mode"),
    ({"mode": None}, "mode"),
    ({"mode": 1}, "mode"),
    ({"depth": "ultra"}, "depth"),
    # max_steps
    ({"max_steps": 0}, "max_steps"),
    ({"max_steps": -3}, "max_steps"),
    ({"max_steps": 51}, "max_steps"),
    ({"max_steps": 2.5}, "max_steps"),
    ({"max_steps": "6"}, "max_steps"),
    ({"max_steps": True}, "max_steps"),
    # approvals.*
    ({"approvals": {"high": "usr"}}, "approvals.high"),
    ({"approvals": {"low": "automatic"}}, "approvals.low"),
    ({"approvals": {"medium": None}}, "approvals.medium"),
    ({"approvals": {"critical": "user"}}, "approvals.critical"),
    ({"approvals": "user"}, "approvals"),
    # model_access: keys of MODEL_ACCESS_PROVIDERS only
    ({"model_access": "claude-account"}, "model_access"),
    ({"model_access": "openai"}, "model_access"),  # a provider, not an access mode
    ({"model_access": None}, "model_access"),
    # budget.*: numbers, finite, bounded
    ({"budget": {"project_usd": -1}}, "budget.project_usd"),
    ({"budget": {"project_usd": MAX_BUDGET_USD + 1}}, "budget.project_usd"),
    ({"budget": {"project_usd": 1e300}}, "budget.project_usd"),  # finite, but switches the gate off
    ({"budget": {"task_usd": "5"}}, "budget.task_usd"),
    ({"budget": {"task_usd": True}}, "budget.task_usd"),
    ({"budget": {"task_usd": None}}, "budget.task_usd"),
    ({"budget": {"max_output_tokens": 0}}, "budget.max_output_tokens"),
    ({"budget": {"max_output_tokens": 128_001}}, "budget.max_output_tokens"),
    ({"budget": {"max_output_tokens": 1500.5}}, "budget.max_output_tokens"),
    ({"budget": {"max_web_searches": -1}}, "budget.max_web_searches"),
    ({"budget": {"max_web_searches": WEB_SEARCH_HARD_CEILING + 1}}, "budget.max_web_searches"),
    ({"budget": {"projet_usd": 2}}, "budget.projet_usd"),  # typo'd key: was silently ignored
    ({"budget": 5}, "budget"),
    # context.max_chars
    ({"context": {"max_chars": "abc"}}, "context.max_chars"),
    ({"context": {"max_chars": 100}}, "context.max_chars"),  # task_context() would build an empty prompt
    ({"context": {"max_chars": 10**9}}, "context.max_chars"),
    # models.<role> shape
    ({"models": "gpt-5"}, "models"),
    ({"models": {"manager": "gpt-5"}}, "models.manager"),
    ({"models": {"manager": None}}, "models.manager"),
    ({"models": {"reserch": {"model": "x"}}}, "models.reserch"),  # not a role any agent can use
    ({"models": {"manager": {"provider": ""}}}, "models.manager.provider"),
    ({"models": {"manager": {"provider": None}}}, "models.manager.provider"),
    ({"models": {"manager": {"model": 5}}}, "models.manager.model"),
    ({"models": {"manager": {"model": "claude opus"}}}, "models.manager.model"),
    ({"models": {"manager": {"effort": "extreme"}}}, "models.manager.effort"),
    ({"models": {"manager": {"temperature": 0.2}}}, "models.manager.temperature"),
    # unknown top-level key (rejected before this change too)
    ({"colour": "red"}, "colour"),
]


@pytest.mark.parametrize("body,path", REJECTED)
def test_invalid_value_is_rejected_and_the_layer_is_left_unchanged(client, project, body, path):
    url = f"/settings/project/{project['id']}"
    before = {"budget": {"task_usd": 0.5}}
    assert client.put(url, json=before).status_code == 200
    r = client.put(url, json=body)
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert isinstance(detail, str) and detail.startswith("Invalid settings: ")  # the Settings page shows it as-is
    assert f"{path}:" in detail
    assert client.get(url).json()["values"] == before


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_budget_is_rejected(client, project, literal):
    """Python's JSON parser accepts these; stored, they made the budget gate's comparison never true."""
    url = f"/settings/project/{project['id']}"
    r = client.put(url, content='{"budget": {"project_usd": %s}}' % literal, headers={"content-type": "application/json"})
    assert r.status_code == 422, r.text
    assert "budget.project_usd: Input should be a finite number" in r.json()["detail"]
    assert client.get(url).json()["values"] == {}


def test_error_names_every_bad_key_in_one_line(client, project):
    r = client.put(f"/settings/project/{project['id']}", json={
        "mode": "auto",
        "budget": {"project_usd": -1, "projet_usd": 1},
        "models": {"manager": {"effort": "extreme"}},
    })
    detail = r.json()["detail"]
    assert "mode: Input should be 'automatic' or 'manual_learning' (got 'auto')" in detail
    assert "budget.project_usd: Input should be greater than or equal to 0 (got -1)" in detail
    assert "budget.projet_usd: unknown setting" in detail
    assert "models.manager.effort: Input should be 'low', 'medium', 'high', 'xhigh' or 'max' (got 'extreme')" in detail


def test_error_list_is_capped(client, project):
    r = client.put(f"/settings/project/{project['id']}", json={f"k{i}": i for i in range(15)})
    assert r.status_code == 422 and r.json()["detail"].endswith("; and 5 more")


ACCEPTED = [
    {},
    # What the web settings form sends after deleting keys to inherit them (it leaves the parent dict).
    {"budget": {}},
    {"approvals": {}},
    {"context": {}},
    {"models": {}},
    {"models": {"manager": {}}},
    # Partial values, one key at a time.
    {"mode": "automatic"},
    {"mode": "manual_learning"},
    {"depth": "quick"},
    {"max_steps": 1},
    {"max_steps": 50},
    {"approvals": {"high": "user"}},
    {"approvals": {"low": "manager", "medium": "auto"}},
    {"model_access": "ollama"},
    {"model_access": "api_key"},
    {"budget": {"task_usd": 0.5}},
    {"budget": {"project_usd": 2}},  # an int where a float is expected
    {"budget": {"project_usd": 0}},  # blocks every paid call: a legitimate spend freeze
    {"budget": {"project_usd": MAX_BUDGET_USD}},
    {"budget": {"max_output_tokens": 128_000}},
    {"budget": {"max_web_searches": 0}},  # turns web search off
    {"context": {"max_chars": 500}},
    {"models": {"coding": {"effort": "xhigh"}}},
    {"models": {"cheap": {"effort": None}}},  # null effort = send none, like DEFAULTS' cheap role
    {"models": {"research": {"provider": "ollama", "model": "qwen2.5:7b-instruct"}}},
    {"models": {"manager": {"provider": "fake", "model": "claude-opus-5"}}},
]


@pytest.mark.parametrize("body", ACCEPTED)
def test_valid_partial_layer_is_stored_exactly_as_sent(client, project, body):
    url = f"/settings/project/{project['id']}"
    r = client.put(url, json=body)
    assert r.status_code == 200, r.text
    assert r.json()["values"] == body
    assert client.get(url).json()["values"] == body


def test_every_field_the_web_form_sets_is_accepted_and_resolved(client, project):
    pid = project["id"]
    full = {
        "mode": "manual_learning",
        "depth": "deep",
        "max_steps": 8,
        "models": {role: {"provider": "anthropic", "model": "claude-sonnet-5", "effort": "low"} for role in MODEL_ROLES},
        "model_access": "api_key",
        "budget": {"project_usd": 12.5, "task_usd": 2, "max_output_tokens": 4000, "max_web_searches": 3},
        "context": {"max_chars": 8000},
        "approvals": {"low": "auto", "medium": "manager", "high": "user"},
    }
    assert client.put(f"/settings/project/{pid}", json=full).status_code == 200
    assert client.get("/settings/resolved", params={"project_id": pid}).json() == full


def test_emptied_nested_dict_inherits_again(client, project):
    url, pid = f"/settings/project/{project['id']}", project["id"]
    assert client.put(url, json={"budget": {"task_usd": 0.5}}).status_code == 200
    assert client.get("/settings/resolved", params={"project_id": pid}).json()["budget"]["task_usd"] == 0.5
    assert client.put(url, json={"budget": {}}).status_code == 200  # the form deleted the key
    assert client.get("/settings/resolved", params={"project_id": pid}).json()["budget"]["task_usd"] == DEFAULTS["budget"]["task_usd"]


def test_schema_mirrors_defaults():
    """Drift guard: every key in DEFAULTS is validated, and the defaults are themselves a valid layer."""
    SettingsLayerIn.model_validate(DEFAULTS)
    assert set(SettingsLayerIn.model_fields) == set(DEFAULTS)
    for key, part in {"approvals": ApprovalsLayer, "budget": BudgetLayer, "context": ContextLayer}.items():
        assert set(part.model_fields) == set(DEFAULTS[key])
    assert set(ApprovalsLayer.model_fields) == {r.value for r in RiskLevel}
    assert set(DEFAULTS["models"]) == MODEL_ROLES
    for cfg in DEFAULTS["models"].values():
        assert set(cfg) == set(ModelRouteLayer.model_fields)


# ---------- the reported symptoms, end to end ----------


def test_mode_typo_can_no_longer_override_an_automatic_projects_mode(client, session_factory, project):
    pid = project["id"]  # created "automatic" (the API default)
    assert client.put(f"/settings/project/{pid}", json={"mode": "auto"}).status_code == 422
    assert client.get("/settings/resolved", params={"project_id": pid}).json()["mode"] == "automatic"

    t = _ready_task(client, pid, risk="low")
    register_researcher(client)
    fake = use_fake(client, session_factory, [CHECKPOINT, GOOD_PLAN])
    assert client.post(f"/projects/{pid}/tasks/{t['id']}/checkpoint").json()["auto_decided"] is True
    assert client.post(f"/projects/{pid}/plan").status_code == 200
    assert "Mode: automatic\n" in fake.requests[-1].user
    app.dependency_overrides.pop(get_gateway, None)


def test_approval_typo_can_no_longer_let_a_high_risk_step_auto_decide(client, session_factory, project):
    pid = project["id"]
    assert client.put(f"/settings/project/{pid}", json={"approvals": {"high": "usr"}}).status_code == 422
    t = _ready_task(client, pid, risk="high")
    use_fake(client, session_factory, [CHECKPOINT])
    assert client.post(f"/projects/{pid}/tasks/{t['id']}/checkpoint").json()["auto_decided"] is False
    assert client.get(f"/projects/{pid}/tasks/{t['id']}").json()["status"] == "READY"
    app.dependency_overrides.pop(get_gateway, None)


@pytest.mark.parametrize("limit, where", [
    (float("nan"), "settings layer"), (float("inf"), "settings layer"),
    (float("inf"), "project column"),  # (NaN here would be stored as NULL: that is SQLite's way with NaN)
])
def test_stored_non_finite_budget_fails_closed(client, session_factory, session, project, limit, where):
    """A row stored before PUT / POST /projects validated values (written directly here) must not switch the gate off.
    Only a SQLite database can hold the settings-layer one (PostgreSQL's json has always refused NaN and Infinity);
    projects.budget is a double precision column, which takes both."""
    if where == "settings layer":
        with postgres_limits.unenforced():
            session.add(SettingsLayer(
                scope=SettingsScope.PROJECT, scope_id=project["id"], values={"budget": {"project_usd": limit}}
            ))
            session.commit()
    else:
        session.get(Project, project["id"]).budget = limit
        session.commit()
    register_researcher(client)
    fake = use_fake(client, session_factory, [GOOD_PLAN])
    r = client.post(f"/projects/{project['id']}/plan")
    assert r.status_code == 402 and "not a finite number" in r.json()["detail"]
    assert fake.requests == []  # refused before any model call
    refusal = [e for e in client.get(f"/projects/{project['id']}/events").json() if e["type"] == "budget.exceeded"]
    assert [e["payload"]["limit_usd"] for e in refusal] == [str(limit)]  # traced, as text: JSON has no NaN/inf
    app.dependency_overrides.pop(get_gateway, None)


# ---------- the project's own budget feeds the same gate ----------


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "-1", '"5"', "true", str(MAX_BUDGET_USD + 1)])
def test_project_budget_must_be_a_real_limit(client, raw):
    user = client.post("/users", json={"email": "b@example.com"}).json()
    body = '{"owner_id": %d, "title": "P", "goal": "g", "budget": %s}' % (user["id"], raw)
    r = client.post("/projects", content=body, headers={"content-type": "application/json"})
    assert r.status_code == 422, r.text
    assert [e["loc"] for e in r.json()["detail"]] == [["body", "budget"]]
    assert client.get("/projects").json() == []


def test_non_finite_input_is_a_422_not_a_500_on_any_endpoint(client, project):
    """FastAPI's default handler echoed the NaN back and failed to serialize it (500)."""
    r = client.post(f"/projects/{project['id']}/tasks", content='{"title": "t", "max_retries": NaN}',
                    headers={"content-type": "application/json"})
    assert r.status_code == 422, r.text
    assert r.json()["detail"][0]["input"] == "nan"


@pytest.mark.parametrize("budget", [None, 0, 0.000001, 25, MAX_BUDGET_USD])
def test_project_budget_accepts_real_limits(client, budget):
    user = client.post("/users", json={"email": "c@example.com"}).json()
    r = client.post("/projects", json={"owner_id": user["id"], "title": "P", "goal": "g", "budget": budget})
    assert r.status_code == 201, r.text
    assert r.json()["budget"] == budget
