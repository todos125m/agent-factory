"""Everything a model replies is stored only after the paid call, so a reply the database can't store is invalid (a
422 with nothing stored), not a 500 at the store that would repeat, and be paid for, on every retry. FakeProvider
only.
"""

import json

import pytest

from app import storable
from app.gateway.providers import FakeProvider
from app.manager import PlanIn
from tests.test_manager import CHECKPOINT, _ready_task
from tests.test_project_pause import _events, _model_calls, _running_task, use_real_gateway
from tests.test_task_run import PLAN, RUN_RESULT

# NUL: PostgreSQL text can't hold one. A lone surrogate: not UTF-8, so no database encodes it.
POISON = pytest.mark.parametrize("poison", ["ok\x00", "ok \ud83d"], ids=["nul", "lone_surrogate"])


def test_what_can_and_cannot_be_stored():
    assert storable.problem("fine, and Persian: سلام 🙂") is None
    assert storable.problem("a\x00b") == "must not contain NUL characters"
    assert storable.problem("a\ud83db") == "is not valid Unicode (lone surrogate)"
    assert storable.clean("a\x00b \ud83d c") == "ab ? c"


@POISON
def test_a_plan_the_database_cannot_store_is_invalid(client, project, monkeypatch, poison):
    pid = project["id"]
    steps = [{**PLAN["steps"][0], "goal": poison}, *PLAN["steps"][1:]]
    use_real_gateway(client, monkeypatch, FakeProvider(replies=[{**PLAN, "steps": steps}]))

    r = client.post(f"/projects/{pid}/plan")
    assert r.status_code == 422 and "invalid plan response" in r.json()["detail"], r.text
    assert client.get(f"/projects/{pid}/tasks").json() == []
    assert "plan.proposed" not in {e["type"] for e in _events(client, pid)}
    assert _model_calls(client, pid) == 1  # the call was made and is logged


@pytest.mark.parametrize("field, limit", [("title", 300), ("agent", 100)])  # tasks.title, tasks.owner
def test_a_plan_step_longer_than_its_column_is_invalid(client, project, monkeypatch, field, limit):
    """PostgreSQL refuses a longer value (SQLite doesn't), after the spend."""
    pid = project["id"]

    def plan_with(length):
        return {**PLAN, "steps": [{**PLAN["steps"][0], field: "x" * length}, *PLAN["steps"][1:]]}

    use_real_gateway(client, monkeypatch, FakeProvider(replies=[plan_with(limit + 1), plan_with(limit)]))
    r = client.post(f"/projects/{pid}/plan")
    assert r.status_code == 422 and "invalid plan response" in r.json()["detail"], r.text
    assert client.post(f"/projects/{pid}/plan").status_code == 200  # exactly at the limit is fine


def test_the_limits_stay_out_of_the_schema_the_provider_gets():
    """The JSON schemas sent with each call are as small and portable as they were: the column limits live in
    validators (strict structured outputs don't take string-length keywords)."""
    assert "maxLength" not in json.dumps(PlanIn.model_json_schema())


@POISON
def test_a_checkpoint_the_database_cannot_store_is_invalid(client, project, monkeypatch, poison):
    pid = project["id"]
    t = _ready_task(client, pid, risk="low")
    options = [{**CHECKPOINT["options"][0], "tradeoff": poison}, *CHECKPOINT["options"][1:]]
    use_real_gateway(client, monkeypatch, FakeProvider(replies=[{**CHECKPOINT, "options": options}]))

    r = client.post(f"/projects/{pid}/tasks/{t['id']}/checkpoint")
    assert r.status_code == 422 and "invalid checkpoint response" in r.json()["detail"], r.text
    assert not {"checkpoint.created", "decision.step"} & {e["type"] for e in _events(client, pid)}
    assert client.get(f"/projects/{pid}/tasks/{t['id']}").json()["status"] == "READY"
    assert _model_calls(client, pid) == 1


@POISON
def test_a_task_result_the_database_cannot_store_is_invalid(client, project, monkeypatch, poison):
    pid = project["id"]
    t = _running_task(client, pid)
    use_real_gateway(client, monkeypatch, FakeProvider(replies=[{**RUN_RESULT, "summary": poison}]))

    r = client.post(f"/projects/{pid}/tasks/{t['id']}/run")
    assert r.status_code == 422 and "invalid task output" in r.json()["detail"], r.text
    task = client.get(f"/projects/{pid}/tasks/{t['id']}").json()
    assert task["status"] == "RUNNING" and task["output"] is None
    assert client.get(f"/projects/{pid}/memory").json() == []
    assert _model_calls(client, pid) == 1
