"""Designing the agent from its approved brief (app/design.py, issue #2 PR3): the deterministic 14-section draft, the
canvas branches, edits / undo / confirmation, the design approval, and the one paid call ("improve with AI") within
the build's own budget."""

from dataclasses import dataclass

import pytest

from app.blueprint import REQUIRED_SECTIONS
from app.design import BRANCHES
from app.gateway.providers import FakeProvider
from app.main import app
from app.models import Approval, FactorySession, ModelCall, RunEvent
from app.routers.manager import get_gateway
from tests.test_factory_intake import TICKET, approve, new, rid, run_through
from tests.test_task_run import use_fake_role

ASSIST = {
    "description": "Sorts support tickets by urgency and drafts a short reply for the support team.",
    "capabilities": ["ticket-triage", "reply-drafting"],
    "test_cases": [
        {"kind": "normal", "input": "charged twice", "expect": "urgent, billing, a draft reply"},
        {"kind": "edge", "input": "(empty)", "expect": "asks what the problem is"},
        {"kind": "failure", "input": "delete my account now", "expect": "refuses: outside its job"},
    ],
}


def designing(client):
    st = approve(client, run_through(client, new(client))).json()
    assert st["session"]["stage"] == "DESIGNING", st
    return st


def post(client, st, path, **body):
    return client.post(f"/factory/sessions/{st['session']['id']}/design/{path}", json={
        **body, "expected_revision": st["session"]["revision"], "request_id": body.pop("request_id", None) or rid()})


def confirm_all(client, st):
    for branch in BRANCHES:
        r = post(client, st, "confirm", branch=branch, blueprint_version=st["design"]["version"])
        assert r.status_code == 200, r.text
        st = r.json()
    return st


# ---------- the draft ----------


def test_approving_the_brief_draws_a_complete_design_without_a_model(client, session_factory):
    st = designing(client)
    d = st["design"]
    assert d["version"] == 1 and d["brief_version"] == st["brief"]["version"] and d["issues"] == []
    bp = d["blueprint"]
    for section in REQUIRED_SECTIONS + ["memory_log", "tech_stack"]:
        assert section in bp, section  # with persona (absent: a specialist), all 14 of the standard
    assert bp["agent_core"]["description"] == TICKET["goal"]
    assert bp["security"]["permissions"] == {"network": False, "repo_write": False, "deploy": False}  # off by default
    assert bp["orchestration"]["approval_gate"] == "always"  # step by step: nothing runs unseen
    assert bp["io_contract"]["expected_output"] == TICKET["expected_outputs"]
    assert [t["kind"] for t in bp["test_plan"]] == ["normal", "edge", "failure"]
    assert {b["status"] for b in d["branches"]} == {"proposed"}
    with session_factory() as s:
        assert s.query(ModelCall).count() == 0


def test_every_section_of_the_standard_sits_on_one_branch():
    covered = [s for sections in BRANCHES.values() for s in sections]
    assert len(covered) == len(set(covered))
    assert set(REQUIRED_SECTIONS + ["persona", "memory_log", "tech_stack"]) <= set(covered)


def test_an_ordinary_owner_is_never_asked_the_technical_choices(client):
    model = next(b for b in designing(client)["design"]["branches"] if b["key"] == "model")
    assert model["values"]["agent_core.model_role"] in ("research", "coding")
    assert model["values"]["data_storage.backend"] == "postgres_pgvector"
    assert model["values"]["scalability.deployment"] == "docker_compose"


# ---------- confirm, edit, undo, approve ----------


def test_confirming_every_branch_then_approving_moves_to_building(client, session_factory):
    st = confirm_all(client, designing(client))
    assert st["session"]["stage"] == "AWAITING_DESIGN_APPROVAL" and st["design"]["can_approve"]
    r = post(client, st, "approve", blueprint_version=st["design"]["version"])
    assert r.status_code == 200, r.text
    done = r.json()
    assert done["session"]["stage"] == "BUILDING"
    with session_factory() as s:
        approval = s.query(Approval).filter_by(resource="blueprint").one()
        assert approval.resource_version == st["design"]["version"] and approval.actor_id is not None
        assert s.query(RunEvent).filter_by(type="design.approved").one().payload["permissions"]["network"] is False


def test_a_design_cannot_be_approved_before_every_branch_is_confirmed(client):
    st = designing(client)
    r = post(client, st, "approve", blueprint_version=st["design"]["version"])
    assert r.status_code == 409 and r.json()["code"] == "stage_not_allowed"


def test_an_edit_makes_a_new_version_and_puts_its_branch_back_to_proposed(client):
    st = confirm_all(client, designing(client))
    r = post(client, st, "edit", branch="identity", changes={"agent_core.description": "Triage and draft replies"})
    assert r.status_code == 200, r.text
    st2 = r.json()
    assert st2["design"]["version"] == st["design"]["version"] + 1
    assert st2["session"]["stage"] == "DESIGNING"
    identity = next(b for b in st2["design"]["branches"] if b["key"] == "identity")
    assert (identity["status"], identity["source"]) == ("proposed", "user")
    assert all(b["status"] == "confirmed" for b in st2["design"]["branches"] if b["key"] != "identity")


def test_a_tool_needs_its_permission_and_blocks_its_branch(client):
    st = designing(client)
    st = post(client, st, "edit", branch="tools", changes={"tools_plugins.tools": ["web_search"]}).json()
    tools = next(b for b in st["design"]["branches"] if b["key"] == "tools")
    assert tools["status"] == "incomplete" and "network" in tools["issues"][0]
    r = post(client, st, "confirm", branch="tools", blueprint_version=st["design"]["version"])
    assert r.status_code == 409 and r.json()["code"] == "branch_incomplete"
    st = post(client, st, "edit", branch="interaction", changes={"security.permissions.network": True}).json()
    assert next(b for b in st["design"]["branches"] if b["key"] == "tools")["status"] == "proposed"


def test_the_approval_gate_cannot_be_looser_than_the_interaction_policy(client):
    st = designing(client)  # step_by_step
    st = post(client, st, "edit", branch="interaction", changes={"orchestration.approval_gate": "auto"}).json()
    interaction = next(b for b in st["design"]["branches"] if b["key"] == "interaction")
    assert interaction["status"] == "incomplete" and "interaction policy" in interaction["issues"][0]
    assert st["design"]["gates_allowed"] == ["always"]


@pytest.mark.parametrize("branch, changes, code", [
    ("identity", {"security.permissions.deploy": True}, "design_value_invalid"),  # not this branch's
    ("knowledge", {"knowledge_boundary.allowed_sources": ["the internet"]}, "design_value_invalid"),
    ("identity", {"agent_core.name": "Not A Slug"}, "design_value_invalid"),
    ("interaction", {"security.budget.max_cost": -1}, "design_value_invalid"),
    ("interaction", {"security.permissions.network": "yes"}, "design_value_invalid"),
    ("nowhere", {"x": 1}, "unknown_branch"),
])
def test_bad_edits_are_refused_and_change_nothing(client, branch, changes, code):
    st = designing(client)
    r = post(client, st, "edit", branch=branch, changes=changes)
    assert r.status_code == 422 and r.json()["code"] == code
    assert client.get(f"/factory/sessions/{st['session']['id']}").json()["design"]["version"] == st["design"]["version"]


def test_undo_restores_an_earlier_version_as_a_new_one(client):
    st = designing(client)
    first = st["design"]["blueprint"]
    st = post(client, st, "edit", branch="identity", changes={"agent_core.description": "Something else"}).json()
    st = post(client, st, "revert", to_version=1).json()
    assert st["design"]["version"] == 3 and st["design"]["reason"] == "reverted"
    assert st["design"]["blueprint"] == first
    assert [h["reason"] for h in st["design"]["history"]] == ["reverted", "edited", "drafted"]


def test_confirming_a_stale_version_is_refused(client):
    st = designing(client)
    newer = post(client, st, "edit", branch="memory", changes={"memory.long_term_sources": "past tickets"}).json()
    r = post(client, newer, "confirm", branch="memory", blueprint_version=st["design"]["version"])
    assert r.status_code == 409 and r.json()["code"] == "stale_version"


def test_changing_the_brief_invalidates_both_approvals_and_redraws(client, session_factory):
    st = confirm_all(client, designing(client))
    st = post(client, st, "approve", blueprint_version=st["design"]["version"]).json()
    assert st["session"]["stage"] == "BUILDING"
    r = client.post(f"/factory/sessions/{st['session']['id']}/answer", json={
        "field": "target_user", "value": "our whole company", "expected_revision": st["session"]["revision"],
        "request_id": rid()})
    st = r.json()
    assert st["session"]["stage"] == "AWAITING_BRIEF_APPROVAL" and st["design"] is None
    with session_factory() as s:
        assert {a.invalidated_reason for a in s.query(Approval)} == {"brief_changed"}
    st = approve(client, st).json()
    assert st["session"]["stage"] == "DESIGNING"
    assert st["design"]["brief_version"] == st["brief"]["version"] and st["design"]["reason"] == "drafted"


def test_a_double_confirm_applies_once(client):
    st = designing(client)
    same = rid()
    a = post(client, st, "confirm", branch="memory", blueprint_version=1, request_id=same).json()
    b = post(client, st, "confirm", branch="memory", blueprint_version=1, request_id=same).json()
    assert b["duplicate"] is True and a["design"]["version"] == b["design"]["version"]


def test_another_account_cannot_touch_the_design(client):
    st = designing(client)
    client.post("/users", json={"email": "other@example.com"})
    assert post(client, st, "confirm", branch="memory", blueprint_version=1).status_code == 404


# ---------- improve with AI: the one paid call, within the build's budget ----------


def test_the_assistant_proposes_and_is_counted_against_the_build(client, session_factory):
    st = designing(client)
    fake = use_fake_role(client, session_factory, "cheap", [ASSIST])
    r = post(client, st, "assist")
    assert r.status_code == 200, r.text
    st2 = r.json()
    d = st2["design"]
    assert d["reason"] == "assisted" and d["blueprint"]["agent_core"]["capabilities"] == ["ticket-triage", "reply-drafting"]
    assert d["blueprint"]["action_engine"]["actions"] == ["ticket-triage", "reply-drafting"]
    identity = next(b for b in d["branches"] if b["key"] == "identity")
    assert (identity["status"], identity["source"]) == ("proposed", "model")  # a suggestion, never confirmed
    assert "Reply language: English (en)" in fake.requests[-1].user
    with session_factory() as s:
        call = s.query(ModelCall).one()
        assert call.factory_session_id == st["session"]["id"] and call.project_id is None
    assert d["budget"]["spent_usd"] == pytest.approx(call.cost_usd)
    app.dependency_overrides.pop(get_gateway, None)


def test_an_exhausted_build_budget_refuses_the_call_before_it_runs(client, session_factory):
    st = designing(client)
    with session_factory() as s:
        s.get(FactorySession, st["session"]["id"]).budget_usd = 0.0
        s.commit()
    fake = use_fake_role(client, session_factory, "cheap", [ASSIST])
    r = post(client, st, "assist")
    assert r.status_code == 402 and r.json()["code"] == "budget_exceeded"
    assert fake.requests == []
    with session_factory() as s:
        assert s.query(RunEvent).filter_by(type="budget.exceeded", factory_session_id=st["session"]["id"]).count() == 1
    assert client.get(f"/factory/sessions/{st['session']['id']}").json()["design"]["version"] == 1
    app.dependency_overrides.pop(get_gateway, None)


@dataclass
class RacingProvider(FakeProvider):
    """Another tab edits the build while the model runs."""

    factory: object = None
    session_id: int = 0

    def complete(self, request):
        with self.factory() as s:
            s.get(FactorySession, self.session_id).revision += 1
            s.commit()
        return super().complete(request)


def test_a_suggestion_for_a_build_that_changed_meanwhile_is_discarded(client, session_factory):
    st = designing(client)
    racing = RacingProvider(replies=[ASSIST], factory=session_factory, session_id=st["session"]["id"])

    def override():
        from app.gateway.service import Gateway
        with session_factory() as s:
            yield Gateway(s, providers={"fake": racing})

    app.dependency_overrides[get_gateway] = override
    client.put("/settings/global/0", json={"models": {"cheap": {"provider": "fake", "model": "claude-haiku-4-5"}}})
    r = post(client, st, "assist")
    assert r.status_code == 409 and r.json()["code"] == "stale_revision"
    with session_factory() as s:
        assert s.query(RunEvent).filter_by(type="design.assist_discarded").count() == 1
        assert s.query(ModelCall).count() == 1  # the paid call is recorded, its result not applied
    app.dependency_overrides.pop(get_gateway, None)


def test_an_invalid_suggestion_changes_nothing(client, session_factory):
    st = designing(client)
    use_fake_role(client, session_factory, "cheap", [{"description": "x", "capabilities": [], "test_cases": []}])
    r = post(client, st, "assist")
    assert r.status_code == 422 and r.json()["code"] == "request_refused"
    assert client.get(f"/factory/sessions/{st['session']['id']}").json()["design"]["version"] == 1
    app.dependency_overrides.pop(get_gateway, None)
