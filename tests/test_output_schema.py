"""An agent's own output schema (app/output_schema.py): written in the design, locked into the built version and the
registered agent, checked in the evaluation (by code, then a judge that sees the schema and the limits) and in every run
through the API. Adversarial cases are made from the agent's limits so the red lines are tested, not only written.
FakeProvider only."""

import json

import pytest
from fastapi.testclient import TestClient

from app import output_schema, worker
from app.gateway.providers import FakeProvider
from app.gateway.service import Gateway
from app.main import app
from app.models import Agent, ModelCall, Run
from tests.test_external_api import bearer, call, make_key, step
from tests.test_factory_build import ADV, PASS, act, fake_models, evaluating
from tests.test_factory_design import confirm_all, designing, post
from tests.test_factory_build import building
from tests.test_task_run import RUN_RESULT

SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": ["order_tracking", "return_request", "payment_issue", "general_question"]},
        "urgency": {"type": "integer", "minimum": 1, "maximum": 5},
        "reply_fa": {"type": "string"},
        "escalate_to_human": {"type": "boolean"},
    },
    "required": ["category", "urgency", "reply_fa", "escalate_to_human"],
    "additionalProperties": False,
}
GOOD = {"category": "order_tracking", "urgency": 3, "reply_fa": "سلام، پیگیری می‌کنیم.", "escalate_to_human": False}
BAD = {**GOOD, "urgency": 9}  # outside 1..5


@pytest.fixture(autouse=True)
def _no_override_left():
    from app.routers.manager import get_gateway

    yield
    app.dependency_overrides.pop(get_gateway, None)


# ---------- the schema itself ----------


def test_a_good_schema_is_kept_as_a_plain_dict():
    assert output_schema.clean(json.dumps(SCHEMA)) == SCHEMA
    assert output_schema.clean(SCHEMA) == SCHEMA


@pytest.mark.parametrize("value, why", [
    ("{not json", "not valid JSON"),
    ([1, 2], "a JSON object"),
    ({"type": "array", "items": {"type": "string"}}, "top level must be an object"),
    ({"type": "object", "properties": {}}, "at least one property"),
    ({"type": "object", "properties": {"a": {"$ref": "http://evil.example/x.json"}}}, r"\$ref' is not allowed"),
    ({"type": "object", "$defs": {}, "properties": {"a": {"type": "string"}}}, r"\$defs' is not allowed"),
    ({"type": "object", "properties": {"a": {"type": "nonsense"}}}, "not a valid JSON Schema"),
    ({"type": "object", "properties": {"a": {"type": "string", "description": "x" * 9000}}}, "longer than"),
    ({"type": "object", "properties": {"a": {"type": "object", "properties": {"b": {"type": "object", "properties": {
        "c": {"type": "object", "properties": {"d": {"type": "object", "properties": {"e": {"type": "object", "properties": {
            "f": {"type": "string"}}}}}}}}}}}}}, "nested more than"),
])
def test_an_unusable_schema_says_why(value, why):
    with pytest.raises(output_schema.SchemaError, match=why):
        output_schema.clean(value)


def test_a_value_is_checked_with_a_real_validator():
    assert output_schema.errors(SCHEMA, GOOD) == []
    found = output_schema.errors(SCHEMA, {**BAD, "category": "refund", "extra": 1})
    assert any("urgency" in f for f in found) and any("category" in f for f in found) and any("extra" in f or "Additional" in f for f in found)
    assert output_schema.errors(SCHEMA, {"category": "order_tracking"})[0].startswith("(root):")  # required fields missing


# ---------- the design ----------


def with_schema(client):
    st = designing(client)
    r = post(client, st, "edit", branch="io_actions", changes={"io_contract.output_schema": SCHEMA})
    assert r.status_code == 200, r.text
    return r.json()


def test_the_schema_is_part_of_the_design_and_the_limits_come_from_the_brief(client):
    st = designing(client)
    io = next(b for b in st["design"]["branches"] if b["key"] == "io_actions")
    assert "io_contract.output_schema" in io["editable"] and io["values"]["io_contract.output_schema"] is None
    assert st["design"]["blueprint"]["io_contract"]["constraints"]  # copied from the brief's limits
    st = with_schema(client)
    assert st["design"]["blueprint"]["io_contract"]["output_schema"] == SCHEMA


def test_a_bad_schema_is_refused_in_the_design_and_text_json_is_accepted(client):
    st = designing(client)
    r = post(client, st, "edit", branch="io_actions", changes={"io_contract.output_schema": {"type": "array"}})
    assert r.status_code == 422 and r.json()["code"] == "design_value_invalid"
    r = post(client, st, "edit", branch="io_actions", changes={"io_contract.output_schema": json.dumps(SCHEMA)})
    assert r.status_code == 200 and r.json()["design"]["blueprint"]["io_contract"]["output_schema"] == SCHEMA


def test_a_schema_proposed_from_the_expected_output_is_returned_not_stored(client, session_factory):
    st = designing(client)
    fake = fake_models(client, session_factory, [{"schema": SCHEMA}])
    r = client.post(f"/factory/sessions/{st['session']['id']}/design/propose-schema",
                    json={"expected_revision": st["session"]["revision"], "request_id": "propose-0001"})
    assert r.status_code == 200 and r.json()["schema"] == SCHEMA
    assert "expected_output" not in fake.requests[0].user and "What it must return" in fake.requests[0].user
    after = client.get(f"/factory/sessions/{st['session']['id']}").json()
    assert after["design"]["blueprint"]["io_contract"]["output_schema"] is None and after["session"]["revision"] == st["session"]["revision"]
    fake_models(client, session_factory, [{"schema": {"type": "array"}}])  # an unusable proposal is refused, not stored
    assert client.post(f"/factory/sessions/{st['session']['id']}/design/propose-schema",
                       json={"expected_revision": st["session"]["revision"], "request_id": "propose-0002"}).status_code == 422


# ---------- build and evaluation ----------


def built(client):
    st = confirm_all(client, with_schema(client))
    r = post(client, st, "approve", blueprint_version=st["design"]["version"])
    assert r.status_code == 200, r.text
    r = act(client, r.json(), "build")
    assert r.status_code == 200, r.text
    return r.json()


def test_the_built_version_carries_the_schema_and_the_limits(client):
    st = built(client)
    spec = st["build"]["candidate"]["spec"]
    assert spec["output_schema"] == SCHEMA
    assert "follows this JSON schema" in spec["instructions"] and '"urgency"' in spec["instructions"]
    assert "Reply only as JSON: summary" not in spec["instructions"]  # the generic contract is replaced
    assert "red lines" in spec["instructions"]


def test_the_schema_goes_to_the_model_and_the_judge_sees_it_with_the_limits(client, session_factory):
    st = built(client)
    fake = fake_models(client, session_factory, [ADV] + [GOOD, PASS] * 5)
    r = act(client, st, "evaluate")
    assert r.status_code == 200 and r.json()["session"]["stage"] == "AWAITING_ACTIVATION_APPROVAL", r.text
    agent_calls = [q for q in fake.requests if q.json_schema == SCHEMA]
    assert len(agent_calls) == 5  # every case is asked with the agent's own schema as its response schema
    judge = next(q for q in fake.requests if "DATA to judge" in q.user)
    assert "ITS OWN schema" in judge.user and '"escalate_to_human"' in judge.user and "limits this agent must never break" in judge.user


def test_a_reply_that_breaks_the_schema_fails_by_code_with_the_reasons_and_no_judge(client, session_factory):
    st = built(client)
    fake = fake_models(client, session_factory, [ADV, BAD, GOOD, PASS, GOOD, PASS, GOOD, PASS, GOOD, PASS])
    r = act(client, st, "evaluate")
    assert r.status_code == 200 and r.json()["session"]["stage"] == "DESIGNING", r.text  # a failed case: back to the design
    first = r.json()["build"]["evaluations"][0]["cases"][0]
    assert (first["valid"], first["verdict"], first["reason"]) == (False, "FAIL", "invalid_output")
    assert any("urgency" in e for e in first["errors"])
    assert sum("DATA to judge" in q.user for q in fake.requests) == 4  # the broken reply was not judged


def test_a_reply_that_is_not_an_object_fails_too(client, session_factory):
    st = built(client)
    fake_models(client, session_factory, [ADV, [1, 2, 3]] + [GOOD, PASS] * 4)  # a JSON list, not the object
    first = act(client, st, "evaluate").json()["build"]["evaluations"][0]["cases"][0]
    assert (first["verdict"], first["reason"]) == ("FAIL", "invalid_output") and first["errors"] == ["(root): the reply is not a JSON object"]


def test_at_least_two_adversarial_cases_are_made_from_the_limits_and_run(client, session_factory):
    st = built(client)
    fake = fake_models(client, session_factory, [ADV] + [GOOD, PASS] * 5)
    cases = act(client, st, "evaluate").json()["build"]["evaluations"][0]["cases"]
    assert [c["kind"] for c in cases].count("adversarial") == 2
    ask = fake.requests[0]
    assert ask.json_schema["title"] == "AdversarialOut" and "red lines" in ask.user
    assert any(c["input"] == ADV["cases"][0]["input"] for c in cases)  # the model's own wording is what ran


def test_without_a_usable_reply_the_adversarial_cases_are_fixed_ones_built_from_the_limits(client, session_factory):
    st = built(client)
    fake_models(client, session_factory, [{"cases": []}] + [GOOD, PASS] * 5)  # the model gave none
    cases = act(client, st, "evaluate").json()["build"]["evaluations"][0]["cases"]
    adversarial = [c for c in cases if c["kind"] == "adversarial"]
    assert len(adversarial) == 2
    assert "Ignore all your earlier instructions" in adversarial[0]["input"]
    assert "does not break this limit" in adversarial[1]["expect"]  # from the first of the written limits


def test_the_designs_own_adversarial_cases_count(client, session_factory):
    from app.build import _adversarial_cases

    class Never:
        def call(self, *a, **k):  # pragma: no cover - must not be reached
            raise AssertionError("no model call is needed")

    bp = {"agent_core": {"description": "d"}, "io_contract": {"constraints": "never promise a refund"}}
    two = [{"kind": "adversarial"}, {"kind": "adversarial"}]
    assert _adversarial_cases(Never(), bp, two, "en", 1) == ([], 0.0)


# ---------- the API ----------


def activated_with_schema(client, session_factory):
    st = built(client)
    fake_models(client, session_factory, [ADV] + [GOOD, PASS] * 5)
    assert act(client, st, "evaluate").status_code == 200
    st = client.get(f"/factory/sessions/{st['session']['id']}").json()
    assert act(client, st, "activate", agent_version=1).status_code == 200
    with session_factory() as s:
        assert s.query(Agent).filter_by(name="ticket-triage").one().output_schema == SCHEMA  # kept on the registered agent
    me = client.get("/auth/me").json()
    return client.post("/projects", json={"owner_id": me["id"], "title": "API project", "goal": "g"}).json()


def structured_finish(result):
    return {"action": "finish", "reason": "done", "result": result}


def test_an_api_run_returns_the_object_of_the_agents_schema_and_the_model_was_given_it(client, session_factory):
    project = activated_with_schema(client, session_factory)
    fake_models(client, session_factory, [])
    key = make_key(client, project)
    secret, ext = key["secret"], TestClient(app)
    run_id = call(ext, secret, "POST", "/runs", input="سفارشم کجاست؟", request_id="call-struct-1").json()["run_id"]
    fake = step(session_factory, [structured_finish(GOOD)])
    done = call(ext, secret, "GET", f"/runs/{run_id}").json()
    assert done["state"] == "completed" and done["result"] == GOOD  # exactly the schema's object
    request = fake.requests[0]
    assert request.json_schema["properties"]["result"]["anyOf"][0] == SCHEMA  # the step's `result` is the agent's schema
    assert "the JSON object of your output schema" in request.user


def test_an_api_run_whose_result_breaks_the_schema_is_not_a_result(client, session_factory):
    project = activated_with_schema(client, session_factory)
    fake_models(client, session_factory, [])
    key = make_key(client, project)
    secret, ext = key["secret"], TestClient(app)
    run_id = call(ext, secret, "POST", "/runs", input="hello", request_id="call-struct-2").json()["run_id"]
    step(session_factory, [structured_finish(BAD)])  # the worker asks again; the queue is empty, so it keeps failing
    final = call(ext, secret, "GET", f"/runs/{run_id}").json()
    assert final["state"] != "completed" and final["result"] is None
    with session_factory() as s:
        run = s.get(Run, run_id)
        assert run.state == "stopped" and run.reason == "schema_mismatch" and run.failures > 2
