"""Building an agent with its owner (app/factory.py, issue #2 PR2): the adaptive intake, the versioned brief and its
approval. No model call anywhere in it (a FakeProvider would record one)."""

import itertools

import pytest

from app.factory import field_specs
from app.models import Approval, BriefRevision, RunEvent

_ids = itertools.count()

TICKET = {  # the issue's acceptance scenario: a support-ticket agent
    "goal": "An agent that reads our customers' support tickets, sets their priority and drafts a reply",
    "target_user": "our three-person support team",
    "problem": "each ticket takes 20 minutes and urgent ones get lost",
    "input_examples": "Customer: I was charged twice for March, please refund one payment.",
    "expected_outputs": "Priority: urgent; category: billing; a three-sentence draft reply",
    "acceptance_criteria": "On 8 of 10 sample tickets the priority is right and the reply needs no edits",
}


def rid():
    return f"req{next(_ids):06d}x"


def new(client, locale="en"):
    r = client.post(f"/factory/sessions?locale={locale}", json={"title": "Ticket triage", "request_id": rid()})
    assert r.status_code == 201, r.text
    return r.json()


def answer(client, st, field, value=None, dont_know=False, **kw):
    body = {"field": field, "value": value, "dont_know": dont_know, "expected_revision": st["session"]["revision"],
            "request_id": kw.pop("request_id", rid())}
    return client.post(f"/factory/sessions/{st['session']['id']}/answer", json=body, **kw)


def run_through(client, st, answers=TICKET):
    """Answer every question it asks from `answers`; stops at a question `answers` has no answer for."""
    while st["question"] is not None and st["question"]["field"] in answers:
        r = answer(client, st, st["question"]["field"], answers[st["question"]["field"]])
        assert r.status_code == 200, r.text
        st = r.json()
    return st


def approve(client, st, **kw):
    body = {"brief_version": st["brief"]["version"], "expected_revision": st["session"]["revision"],
            "request_id": kw.pop("request_id", rid())}
    return client.post(f"/factory/sessions/{st['session']['id']}/approve", json=body)


# ---------- the intake ----------


def test_it_starts_with_what_for_whom_and_what_it_does(client):
    st = new(client)
    assert st["session"]["stage"] == "DRAFT"
    assert st["question"]["field"] == "goal" and st["question"]["text"].startswith("What do you want to build")
    assert st["question"]["can_suggest"] is False  # only the owner can say what to build
    assert st["session"]["interaction_policy"] == "step_by_step"  # building an agent: every step approved
    # what isn't asked is proposed, never passed off as the owner's answer
    assert {f["field"] for f in st["summary"]["proposals"]} == {"constraints", "allowed_sources", "permissions", "budget"}
    assert all(f["status"] == "proposed" and f["source"] == "default" for f in st["summary"]["proposals"])
    assert st["summary"]["from_you"] == []


def test_the_support_ticket_scenario_end_to_end(client, session_factory):
    st = run_through(client, new(client))
    assert st["session"]["stage"] == "AWAITING_BRIEF_APPROVAL" and st["question"] is None
    assert st["can_approve"] is True
    assert {f["field"] for f in st["summary"]["from_you"]} == set(TICKET)
    assert all(f["status"] == "draft" for f in st["summary"]["from_you"])

    r = approve(client, st)
    assert r.status_code == 200, r.text
    done = r.json()
    assert done["session"]["stage"] == "DESIGNING"
    assert all(f["status"] == "confirmed" for f in done["brief"]["fields"].values())
    assert done["brief"]["fields"]["goal"]["value"] == TICKET["goal"]
    assert done["approval"]["version"] == done["brief"]["version"] == st["brief"]["version"] + 1
    with session_factory() as s:
        approval = s.query(Approval).one()
        assert approval.actor_id == client.get("/auth/me").json()["id"]  # a real, signed-in actor
        assert approval.resource == "brief" and approval.invalidated_at is None
        types = [e.type for e in s.query(RunEvent).filter(RunEvent.factory_session_id == st["session"]["id"])]
        assert types.count("factory.answered") == len(TICKET) and "factory.brief_approved" in types
        assert all(e.payload["actor_id"] for e in s.query(RunEvent).filter(RunEvent.factory_session_id.isnot(None)))


def test_one_sentence_and_a_few_short_questions(client):
    """Owner decision d28: the goal, then at most five short questions; the rest is proposed."""
    asked = [s["field"] for s in field_specs() if s.get("asked")]
    assert asked[0] == "goal" and 3 <= len(asked) - 1 <= 5


def test_a_known_field_is_never_asked_again(client):
    st = new(client)
    seen = []
    while st["question"] is not None:
        field = st["question"]["field"]
        assert field not in seen
        seen.append(field)
        st = answer(client, st, field, TICKET[field]).json()


def test_i_dont_know_proposes_or_leaves_an_open_question(client):
    st = answer(client, new(client), "goal", TICKET["goal"]).json()
    st = answer(client, st, "target_user", dont_know=True).json()
    f = st["brief"]["fields"]["target_user"]
    assert (f["status"], f["source"], f["value"]) == ("proposed", "suggestion", "me, the owner")
    st = answer(client, st, "problem", dont_know=True).json()  # no suggestion exists: an open question
    assert st["brief"]["fields"]["problem"]["value"] is None
    assert [u["field"] for u in st["summary"]["unknowns"]] == ["problem"]
    r = answer(client, st, "goal", dont_know=True)
    assert r.status_code == 422 and r.json()["code"] == "answer_required"


@pytest.mark.parametrize("vague", ["idk", "?", "نمی‌دونم", "hm"])
def test_a_vague_answer_is_asked_again(client, vague):
    st = answer(client, new(client), "goal", TICKET["goal"]).json()
    st = answer(client, st, "target_user", vague).json()
    assert st["question"]["field"] == "target_user" and st["question"]["kind"] == "clarify"
    st = answer(client, st, "target_user", "the support team").json()
    assert st["question"]["field"] == "problem" and st["question"]["kind"] == "ask"


def test_a_one_word_goal_is_not_enough(client):
    st = answer(client, new(client), "goal", "chatbot").json()
    assert st["question"] == {**st["question"], "field": "goal", "kind": "clarify"}


def test_a_contradiction_becomes_the_next_question_and_blocks_approval(client):
    answers = {**TICKET, "goal": "An agent that searches the web for competitors' prices every morning"}
    st = run_through(client, new(client), answers)
    assert st["question"]["kind"] == "conflict" and st["question"]["field"] == "constraints"
    assert st["session"]["stage"] == "CLARIFYING" and st["can_approve"] is False
    st = answer(client, st, "constraints", "may read public websites; never logs in or buys anything").json()
    assert st["question"] is None and st["session"]["stage"] == "AWAITING_BRIEF_APPROVAL" and st["can_approve"]


def test_without_brief_approval_nothing_moves_on(client):
    st = answer(client, new(client), "goal", TICKET["goal"]).json()
    r = approve(client, st)
    assert r.status_code == 409 and r.json()["code"] == "stage_not_allowed"
    assert client.get(f"/factory/sessions/{st['session']['id']}").json()["session"]["stage"] == "CLARIFYING"


def test_the_three_levels_reach_the_same_brief(client):
    briefs, helps = [], []
    new(client)  # signs the client in (the server's first account)
    me = client.get("/auth/me").json()["id"]
    for level in ("ordinary", "professional", "developer"):
        client.patch(f"/users/{me}/preferences", json={"audience_level": level})
        st = new(client)
        helps.append((len(st["question"]["help"]), "field_key" in st["question"]))
        st = run_through(client, st)
        briefs.append({k: v["value"] for k, v in st["brief"]["fields"].items()})
    assert briefs[0] == briefs[1] == briefs[2]
    assert helps == [(1, False), (2, False), (2, True)]  # only the explanation differs


def test_resuming_after_a_refresh_asks_the_same_question(client):
    st = answer(client, new(client), "goal", TICKET["goal"]).json()
    again = client.get(f"/factory/sessions/{st['session']['id']}").json()
    assert again["question"] == st["question"] and again["brief"] == st["brief"]


def test_changing_the_language_keeps_the_answers_and_the_place(client):
    st = answer(client, new(client, "fa"), "goal", TICKET["goal"]).json()
    tr = client.get(f"/factory/sessions/{st['session']['id']}?locale=tr").json()
    assert tr["question"]["field"] == "target_user" and tr["question"]["text"].startswith("Onu kim")
    assert tr["brief"] == st["brief"]


# ---------- versions, approvals, races ----------


def test_editing_after_approval_invalidates_only_that_approval_and_reopens_it(client, session_factory):
    st = approve(client, run_through(client, new(client))).json()
    r = answer(client, st, "acceptance_criteria", "9 of 10 sample tickets are prioritised correctly")
    assert r.status_code == 200
    edited = r.json()
    assert edited["session"]["stage"] == "AWAITING_BRIEF_APPROVAL" and edited["approval"] is None
    assert edited["brief"]["fields"]["acceptance_criteria"]["status"] == "draft"
    assert edited["brief"]["fields"]["goal"]["status"] == "confirmed"  # the rest of the approved version stays
    with session_factory() as s:
        old = s.query(Approval).one()
        assert old.invalidated_reason == "brief_changed"
    assert approve(client, edited).json()["session"]["stage"] == "DESIGNING"


def test_an_older_brief_version_cannot_be_approved(client):
    st = run_through(client, new(client))
    stale = {**st, "brief": {**st["brief"], "version": st["brief"]["version"] - 1}}
    r = approve(client, stale)
    assert r.status_code == 409 and r.json()["code"] == "stale_version"


def test_a_second_tab_or_a_late_answer_is_refused_and_changes_nothing(client):
    tab1 = answer(client, new(client), "goal", TICKET["goal"]).json()
    tab2 = dict(tab1)
    tab1 = answer(client, tab1, "target_user", "support team").json()
    r = answer(client, tab2, "target_user", "everyone")  # tab 2 didn't see tab 1's answer
    assert r.status_code == 409 and r.json()["code"] == "stale_revision"
    assert r.json()["params"]["current_revision"] == tab1["session"]["revision"]
    now = client.get(f"/factory/sessions/{tab1['session']['id']}").json()
    assert now["brief"]["fields"]["target_user"]["value"] == "support team"


def test_a_double_submit_applies_once(client, session_factory):
    st = answer(client, new(client), "goal", TICKET["goal"]).json()
    same = rid()
    first = answer(client, st, "target_user", "support team", request_id=same)
    second = answer(client, st, "target_user", "support team", request_id=same)
    assert first.status_code == second.status_code == 200
    assert second.json()["duplicate"] is True and first.json()["duplicate"] is False
    assert second.json()["session"]["revision"] == first.json()["session"]["revision"]
    with session_factory() as s:
        assert s.query(BriefRevision).filter_by(session_id=st["session"]["id"]).count() == 3  # created, goal, target


def test_a_double_approval_approves_once(client, session_factory):
    st = run_through(client, new(client))
    same = rid()
    assert approve(client, st, request_id=same).status_code == 200
    assert approve(client, st, request_id=same).json()["duplicate"] is True
    with session_factory() as s:
        assert s.query(Approval).count() == 1


def test_creating_twice_with_one_request_id_makes_one_build(client):
    same = rid()
    a = client.post("/factory/sessions", json={"request_id": same}).json()
    b = client.post("/factory/sessions", json={"request_id": same}).json()
    assert a["session"]["id"] == b["session"]["id"]
    assert len(client.get("/factory/sessions").json()) == 1


def test_a_paused_build_changes_nothing_until_resumed(client):
    st = answer(client, new(client), "goal", TICKET["goal"]).json()
    sid = st["session"]["id"]
    st = client.post(f"/factory/sessions/{sid}/pause", json={
        "paused": True, "expected_revision": st["session"]["revision"], "request_id": rid()}).json()
    assert st["session"]["stage"] == "PAUSED" and st["question"] is None
    r = answer(client, st, "target_user", "team")
    assert r.status_code == 409 and r.json()["code"] == "session_paused"
    st = client.post(f"/factory/sessions/{sid}/pause", json={
        "paused": False, "expected_revision": st["session"]["revision"], "request_id": rid()}).json()
    assert st["session"]["stage"] == "CLARIFYING" and st["question"]["field"] == "target_user"


def test_a_cancelled_build_is_final(client):
    st = new(client)
    sid = st["session"]["id"]
    st = client.post(f"/factory/sessions/{sid}/cancel", json={
        "expected_revision": st["session"]["revision"], "request_id": rid()}).json()
    assert st["session"]["stage"] == "CANCELLED"
    assert answer(client, st, "goal", TICKET["goal"]).json()["code"] == "stage_not_allowed"


def test_request_changes_reopens_the_intake(client):
    st = run_through(client, new(client))
    st = client.post(f"/factory/sessions/{st['session']['id']}/request-changes", json={
        "expected_revision": st["session"]["revision"], "request_id": rid(), "note": "the audience is wrong"}).json()
    assert st["session"]["stage"] == "CLARIFYING"


@pytest.mark.parametrize("body, code", [
    ({"field": "nope", "value": "x"}, "unknown_field"),
    ({"field": "goal", "value": "   "}, "answer_required"),
    ({"field": "goal", "value": "x" * 4001}, "invalid_request"),
])
def test_bad_answers_are_refused_with_a_code(client, body, code):
    st = new(client)
    r = client.post(f"/factory/sessions/{st['session']['id']}/answer", json={
        **body, "expected_revision": st["session"]["revision"], "request_id": rid()})
    assert r.status_code == 422 and r.json()["code"] == code


def test_another_account_cannot_see_or_touch_a_build(client):
    st = new(client)
    sid = st["session"]["id"]
    client.post("/users", json={"email": "other@example.com"})  # the client now acts as this second account
    assert client.get(f"/factory/sessions/{sid}").status_code == 404
    assert answer(client, st, "goal", TICKET["goal"]).status_code == 404
    assert client.get("/factory/sessions").json() == []


def test_the_intake_makes_no_model_call(client, session_factory):
    from app.models import ModelCall

    approve(client, run_through(client, new(client)))
    with session_factory() as s:
        assert s.query(ModelCall).count() == 0
