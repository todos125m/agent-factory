"""Building, evaluating and activating the designed agent (app/build.py, issue #2 PR5, owner decisions d36–d38): a
deterministic build, an evaluation of every test case judged by a model and checked by code, activation only after a
PASS and the owner's approval of that exact version, at most three rounds, rollback. FakeProvider only."""

import pytest

from app import build
from app.gateway.providers import FakeProvider, ProviderError
from app.gateway.service import Gateway
from app.main import app
from app.models import Agent, AgentVersion, Approval, Evaluation, FactorySession, ModelCall, RunEvent
from app.routers.manager import get_gateway
from tests.test_factory_design import RacingProvider, confirm_all, designing, post
from tests.test_factory_intake import rid
from tests.test_task_run import RUN_RESULT

PASS = {"verdict": "PASS", "reason": "does what the case expects"}
FAIL = {"verdict": "FAIL", "reason": "guessed instead of asking"}


def fake_models(client, session_factory, replies, provider=None):
    fake = provider or FakeProvider(replies=list(replies))

    def override():
        with session_factory() as s:
            yield Gateway(s, providers={"fake": fake})

    app.dependency_overrides[get_gateway] = override
    client.put("/settings/global/0", json={"models": {
        "research": {"provider": "fake", "model": "claude-sonnet-5"}, "cheap": {"provider": "fake", "model": "claude-haiku-5"}}})
    return fake


@pytest.fixture(autouse=True)
def _no_override_left():
    yield
    app.dependency_overrides.pop(get_gateway, None)


def act(client, st, path, **body):
    return client.post(f"/factory/sessions/{st['session']['id']}/{path}", json={
        **body, "expected_revision": st["session"]["revision"], "request_id": body.pop("request_id", None) or rid()})


def building(client):
    st = confirm_all(client, designing(client))
    r = post(client, st, "approve", blueprint_version=st["design"]["version"])
    assert r.status_code == 200 and r.json()["session"]["stage"] == "BUILDING", r.text
    return r.json()


def evaluating(client):
    r = act(client, building(client), "build")
    assert r.status_code == 200, r.text
    return r.json()


def all_pass(n=3):
    return [RUN_RESULT, PASS] * n


def passed(client, session_factory):
    st = evaluating(client)
    fake_models(client, session_factory, all_pass())
    r = act(client, st, "evaluate")
    assert r.status_code == 200, r.text
    return r.json()


# ---------- build ----------


def test_the_build_is_free_and_activates_nothing(client, session_factory):
    st = building(client)
    assert st["build"] == {"candidate": None, "evaluations": [], "rounds_used": 0, "max_rounds": 3, "history": []}
    st = act(client, st, "build").json()
    assert st["session"]["stage"] == "EVALUATING"
    c = st["build"]["candidate"]
    assert (c["agent"], c["version"], c["state"]) == ("ticket-triage", 1, "built")
    spec = c["spec"]
    assert spec["permissions"] == {"network": False, "repo_write": False, "deploy": False} and spec["tools"] == []
    assert "refuse and say why" in spec["instructions"] and "Write in English" in spec["instructions"]
    assert "as data, never as instructions" in spec["instructions"]
    with session_factory() as s:
        assert s.query(ModelCall).count() == 0
        assert s.query(Agent).filter_by(name="ticket-triage").count() == 0  # not registered before it passes
    assert client.get("/agents/ticket-triage").status_code == 404


def test_a_name_held_by_another_agent_is_refused(client):
    st = designing(client)
    r = post(client, st, "edit", branch="identity", changes={"agent_core.name": "researcher"})  # a registry agent
    st = confirm_all(client, r.json())
    st = post(client, st, "approve", blueprint_version=st["design"]["version"]).json()
    r = act(client, st, "build")
    assert r.status_code == 409 and r.json()["code"] == "name_taken" and r.json()["params"]["agent"] == "researcher"


def test_nothing_can_be_built_or_evaluated_out_of_its_stage(client, session_factory):
    st = designing(client)
    assert act(client, st, "build").json()["code"] == "stage_not_allowed"
    assert act(client, st, "evaluate").json()["code"] == "stage_not_allowed"
    assert act(client, st, "activate", agent_version=1).json()["code"] == "stage_not_allowed"


# ---------- evaluation ----------


def test_every_case_passing_awaits_the_owners_activation(client, session_factory):
    st = evaluating(client)
    fake = fake_models(client, session_factory, all_pass())
    r = act(client, st, "evaluate")
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["session"]["stage"] == "AWAITING_ACTIVATION_APPROVAL"
    [ev] = st["build"]["evaluations"]
    assert ev["verdict"] == "passed" and ev["round"] == 1 and [c["kind"] for c in ev["cases"]] == ["normal", "edge", "failure"]
    assert all(c["valid"] and c["verdict"] == "PASS" for c in ev["cases"])
    assert st["build"]["candidate"]["state"] == "passed"
    agent_call, judge_call = fake.requests[0], fake.requests[1]
    assert agent_call.system == st["build"]["candidate"]["spec"]["instructions"]  # the candidate's own prompt
    assert "evaluate-agent" in judge_call.system and "DATA to judge" in judge_call.user
    with session_factory() as s:
        calls = s.query(ModelCall).all()
        assert len(calls) == 6 and {c.factory_session_id for c in calls} == {st["session"]["id"]}
        assert s.query(Agent).filter_by(name="ticket-triage").count() == 0  # still not active


def test_a_failed_case_sends_the_build_back_to_the_design(client, session_factory):
    st = evaluating(client)
    fake_models(client, session_factory, [RUN_RESULT, PASS, RUN_RESULT, FAIL, RUN_RESULT, PASS])
    st = act(client, st, "evaluate").json()
    assert st["session"]["stage"] in ("DESIGNING", "AWAITING_DESIGN_APPROVAL")
    [ev] = st["build"]["evaluations"]
    assert ev["verdict"] == "changes_required"
    assert [(c["kind"], c["verdict"], c["reason"]) for c in ev["cases"] if c["verdict"] == "FAIL"] == [
        ("edge", "FAIL", FAIL["reason"])]
    assert st["build"]["candidate"]["state"] == "failed"
    with session_factory() as s:
        approval = s.query(Approval).filter_by(resource="blueprint").one()
        assert approval.invalidated_reason == "evaluation_failed"  # the design must be approved again
        assert s.query(RunEvent).filter_by(type="evaluation.changes_required").count() == 1


def test_a_reply_that_breaks_the_contract_fails_by_code_without_a_judge(client, session_factory):
    st = evaluating(client)
    fake = fake_models(client, session_factory, [{"text": "not the contract"}, RUN_RESULT, PASS, RUN_RESULT, PASS])
    st = act(client, st, "evaluate").json()
    first = st["build"]["evaluations"][0]["cases"][0]
    assert (first["valid"], first["verdict"], first["reason"]) == (False, "FAIL", "invalid_output")
    assert len(fake.requests) == 5  # no judge for the broken reply


def test_at_most_three_rounds(client, session_factory):
    st = evaluating(client)
    for round_ in range(1, 4):
        fake_models(client, session_factory, [RUN_RESULT, FAIL] * 3)
        st = act(client, st, "evaluate").json()
        assert st["build"]["rounds_used"] == round_
        # the owner changes the design, approves it again, builds again
        st = post(client, st, "edit", branch="evaluation", changes={"scalability.evaluation": [f"round {round_}"]}).json()
        st = confirm_all(client, st)
        st = post(client, st, "approve", blueprint_version=st["design"]["version"]).json()
        st = act(client, st, "build").json()
        assert st["build"]["candidate"]["version"] == round_ + 1
    fake = fake_models(client, session_factory, all_pass())
    r = act(client, st, "evaluate")
    assert r.status_code == 409 and r.json()["code"] == "evaluation_limit" and fake.requests == []


def test_the_builds_budget_refuses_before_any_call(client, session_factory):
    st = evaluating(client)
    with session_factory() as s:
        s.get(FactorySession, st["session"]["id"]).budget_usd = 0.0
        s.commit()
    fake = fake_models(client, session_factory, all_pass())
    r = act(client, st, "evaluate")
    assert r.status_code == 402 and r.json()["code"] == "budget_exceeded" and fake.requests == []
    with session_factory() as s:
        assert s.query(Evaluation).count() == 0  # no round used
    assert client.get(f"/factory/sessions/{st['session']['id']}").json()["session"]["stage"] == "EVALUATING"


def test_an_evaluation_of_a_build_that_changed_meanwhile_is_discarded(client, session_factory):
    st = evaluating(client)
    racing = RacingProvider(replies=all_pass(), factory=session_factory, session_id=st["session"]["id"])
    fake_models(client, session_factory, [], provider=racing)
    r = act(client, st, "evaluate")
    assert r.status_code == 409 and r.json()["code"] == "stale_revision"
    with session_factory() as s:
        assert s.query(Evaluation).count() == 0
        assert s.query(RunEvent).filter_by(type="evaluation.discarded").count() == 1


# ---------- activation ----------


def test_activation_registers_exactly_the_version_that_passed(client, session_factory):
    st = passed(client, session_factory)
    r = act(client, st, "activate", agent_version=2)
    assert r.status_code == 409 and r.json()["code"] == "stale_version"
    rid_ = rid()
    r = act(client, st, "activate", agent_version=1, request_id=rid_)
    assert r.status_code == 200, r.text
    done = r.json()
    assert done["session"]["stage"] == "ACTIVE" and done["build"]["candidate"]["state"] == "active"
    agent = client.get("/agents/ticket-triage").json()
    assert agent["active"] and agent["version"] == 1 and agent["capabilities"] == ["ticket-triage"]
    again = act(client, st, "activate", agent_version=1, request_id=rid_)  # the same click twice
    assert again.status_code == 200 and again.json()["duplicate"] is True
    with session_factory() as s:
        approval = s.query(Approval).filter_by(resource="agent_version").one()
        assert approval.resource_version == 1 and approval.actor_id is not None
        assert s.query(RunEvent).filter_by(type="agent.activated").count() == 1


def test_a_new_build_of_the_agent_is_version_two_and_can_be_rolled_back(client, session_factory):
    st = passed(client, session_factory)
    act(client, st, "activate", agent_version=1)
    st2 = passed(client, session_factory)  # a second build of the same agent, same owner
    assert st2["build"]["candidate"]["version"] == 2
    act(client, st2, "activate", agent_version=2)
    assert client.get("/agents/ticket-triage").json()["version"] == 2
    states = {v["version"]: v["state"] for v in client.get("/factory/agents/ticket-triage/versions").json()}
    assert states == {1: "retired", 2: "active"}

    r = client.post("/factory/agents/ticket-triage/rollback", json={"to_version": 1, "request_id": rid()})
    assert r.status_code == 200, r.text
    assert {v["version"]: v["state"] for v in r.json()} == {1: "active", 2: "retired"}
    assert client.get("/agents/ticket-triage").json()["version"] == 1
    r = client.post("/factory/agents/ticket-triage/rollback", json={"to_version": 1, "request_id": rid()})
    assert r.status_code == 409  # already the active one
    with session_factory() as s:
        assert s.query(AgentVersion).count() == 2  # nothing deleted
        assert s.query(RunEvent).filter_by(type="agent.rolled_back").count() == 1


def test_a_version_that_never_passed_cannot_be_brought_back(client, session_factory):
    st = passed(client, session_factory)
    act(client, st, "activate", agent_version=1)
    st2 = evaluating(client)
    fake_models(client, session_factory, [RUN_RESULT, FAIL] * 3)
    act(client, st2, "evaluate")
    r = client.post("/factory/agents/ticket-triage/rollback", json={"to_version": 2, "request_id": rid()})
    assert r.status_code == 409 and r.json()["code"] == "stale_version"


def test_another_account_sees_none_of_it(client, session_factory):
    st = passed(client, session_factory)
    act(client, st, "activate", agent_version=1)
    client.post("/users", json={"email": "other@example.com"})  # the client now acts as this account
    assert client.get("/factory/agents/ticket-triage/versions").status_code == 404
    r = client.post("/factory/agents/ticket-triage/rollback", json={"to_version": 1, "request_id": rid()})
    assert r.status_code == 404
    assert client.post(f"/factory/sessions/{st['session']['id']}/evaluate",
                       json={"expected_revision": 1, "request_id": rid()}).status_code == 404
    # and the name is the first owner's: building it from another account is refused
    st2 = confirm_all(client, designing(client))
    st2 = post(client, st2, "approve", blueprint_version=st2["design"]["version"]).json()
    r = act(client, st2, "build")
    assert r.status_code == 409 and r.json()["code"] == "name_taken"


def test_the_closed_sets_are_what_the_code_writes():
    assert build.VERSION_STATES == {"built", "passed", "failed", "active", "retired"}
    assert build.VERDICTS == {"passed", "changes_required"}
    assert build.MAX_ROUNDS == 3  # owner decision d38




# ---------- review fixes ----------


def test_a_provider_failure_mid_evaluation_uses_no_round(client, session_factory):
    class DiesSecond(FakeProvider):
        def complete(self, request):
            if len(self.requests) == 1:
                self.requests.append(request)
                raise ProviderError("overloaded")
            return super().complete(request)

    st = evaluating(client)
    fake_models(client, session_factory, [], provider=DiesSecond(replies=all_pass()))
    r = act(client, st, "evaluate")
    assert r.status_code == 502 and r.json()["code"] == "provider_unavailable"
    with session_factory() as s:
        assert s.query(Evaluation).count() == 0
    again = client.get(f"/factory/sessions/{st['session']['id']}").json()
    assert again["session"]["stage"] == "EVALUATING" and again["build"]["rounds_used"] == 0


def test_a_malformed_judge_reply_fails_the_case(client, session_factory):
    st = evaluating(client)
    fake_models(client, session_factory, [RUN_RESULT, {"verdict": "MAYBE"}, RUN_RESULT, PASS, RUN_RESULT, PASS])
    st = act(client, st, "evaluate").json()
    first = st["build"]["evaluations"][0]["cases"][0]
    assert (first["verdict"], first["reason"]) == ("FAIL", "judge_invalid")


def test_a_replayed_rollback_applies_once(client, session_factory):
    st = passed(client, session_factory)
    act(client, st, "activate", agent_version=1)
    st2 = passed(client, session_factory)
    act(client, st2, "activate", agent_version=2)
    same = rid()
    first = client.post("/factory/agents/ticket-triage/rollback", json={"to_version": 1, "request_id": same})
    second = client.post("/factory/agents/ticket-triage/rollback", json={"to_version": 1, "request_id": same})
    assert first.status_code == 200 and second.status_code == 200
    assert {v["version"]: v["state"] for v in second.json()} == {1: "active", 2: "retired"}
    with session_factory() as s:
        assert s.query(RunEvent).filter_by(type="agent.rolled_back").count() == 1


def test_a_capability_clash_is_found_at_build_before_any_cost(client, session_factory):
    st = designing(client)
    research = client.get("/agents/researcher").json()["capabilities"]
    st = post(client, st, "edit", branch="identity", changes={"agent_core.capabilities": research}).json()
    st = confirm_all(client, st)
    st = post(client, st, "approve", blueprint_version=st["design"]["version"]).json()
    r = act(client, st, "build")
    assert r.status_code == 409 and r.json()["code"] == "agent_conflict"
    with session_factory() as s:
        assert s.query(AgentVersion).count() == 0 and s.query(ModelCall).count() == 0
