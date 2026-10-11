"""The owner's display preferences (app/preferences.py, docs/HUMAN_GUIDED_FACTORY.md §4): language, explanation level
and code visibility are stored per account, change without resetting anything, and grant nothing."""

import pytest

from app.main import app
from app.models import UserPreference
from app.routers.manager import get_gateway
from tests.test_manager import CHECKPOINT, GOOD_PLAN, _ready_task, register_researcher, use_fake


def _user(client, email="p@example.com"):
    return client.post("/users", json={"email": email}).json()["id"]


def test_nothing_chosen_yet_reads_as_unchosen_with_proposed_defaults(client):
    uid = _user(client)
    body = client.get(f"/users/{uid}/preferences").json()
    assert body["ui_locale"] is None and body["audience_level"] is None and body["code_visibility"] is None
    assert body["effective"] == {"ui_locale": "fa", "dir": "rtl", "audience_level": "ordinary", "code_visibility": "hidden"}
    assert body["updated_at"] is None


def test_choices_are_stored_and_change_one_key_at_a_time(client):
    uid = _user(client)
    r = client.patch(f"/users/{uid}/preferences", json={"ui_locale": "en"})
    assert r.status_code == 200, r.text
    assert r.json()["effective"]["dir"] == "ltr"
    client.patch(f"/users/{uid}/preferences", json={"audience_level": "developer"})
    body = client.get(f"/users/{uid}/preferences").json()
    assert body["ui_locale"] == "en"  # kept: a PATCH sets only what it sends
    assert body["audience_level"] == "developer"
    assert body["effective"]["code_visibility"] == "visible"  # follows the level until chosen


def test_code_visibility_overrides_the_level_and_null_follows_it_again(client):
    uid = _user(client)
    client.patch(f"/users/{uid}/preferences", json={"audience_level": "ordinary", "code_visibility": "visible"})
    assert client.get(f"/users/{uid}/preferences").json()["effective"]["code_visibility"] == "visible"
    client.patch(f"/users/{uid}/preferences", json={"code_visibility": None})
    body = client.get(f"/users/{uid}/preferences").json()
    assert body["code_visibility"] is None and body["effective"]["code_visibility"] == "hidden"


@pytest.mark.parametrize("body", [
    {"ui_locale": "de"},  # not offered
    {"ui_locale": None},  # a chosen language is changed, not unchosen
    {"audience_level": None},
    {"audience_level": "expert"},
    {"code_visibility": "sometimes"},
    {"unknown": 1},
    {"ui_locale": "fa\x00"},
])
def test_invalid_preferences_are_a_422_and_change_nothing(client, body):
    uid = _user(client)
    client.patch(f"/users/{uid}/preferences", json={"ui_locale": "tr"})
    r = client.patch(f"/users/{uid}/preferences", json=body)
    assert r.status_code == 422
    assert r.json()["code"] == "invalid_request"
    assert client.get(f"/users/{uid}/preferences").json()["ui_locale"] == "tr"


def test_unknown_user_is_a_coded_404(client):
    for r in (client.get("/users/999/preferences"), client.patch("/users/999/preferences", json={"ui_locale": "en"})):
        assert r.status_code == 404
        assert r.json()["code"] == "not_found" and r.json()["params"] == {"resource": "user"}


def test_a_value_this_version_no_longer_offers_reads_as_unchosen(client, session_factory):
    uid = _user(client)
    with session_factory() as s:
        s.add(UserPreference(user_id=uid, ui_locale="de", audience_level="guru", code_visibility="x"))
        s.commit()
    body = client.get(f"/users/{uid}/preferences").json()
    assert (body["ui_locale"], body["audience_level"], body["code_visibility"]) == (None, None, None)
    assert body["effective"]["ui_locale"] == "fa"


def test_preferences_are_per_account(client):
    a, b = _user(client, "a@example.com"), _user(client, "b@example.com")
    client.patch(f"/users/{a}/preferences", json={"ui_locale": "tr"})
    assert client.get(f"/users/{b}/preferences").json()["ui_locale"] is None


def test_changing_language_resets_no_plan_checkpoint_or_stage(client, session_factory):
    """Changing language mid-work keeps every answer, approval and stage: they are stored apart from how they show."""
    uid = _user(client)
    pid = client.post("/projects", json={"owner_id": uid, "title": "P", "goal": "g", "mode": "manual_learning"}).json()["id"]
    register_researcher(client)
    use_fake(client, session_factory, [GOOD_PLAN, CHECKPOINT])
    client.post(f"/projects/{pid}/plan")
    client.post(f"/projects/{pid}/stage", json={"stage": "DISCOVERY"})
    t = _ready_task(client, pid, risk="low")
    client.post(f"/projects/{pid}/tasks/{t['id']}/checkpoint")
    before = (client.get("/inbox").json(), client.get(f"/projects/{pid}").json(), client.get(f"/projects/{pid}/events").json())

    for locale in ("en", "tr", "fa"):
        assert client.patch(f"/users/{uid}/preferences", json={"ui_locale": locale}).status_code == 200

    after = (client.get("/inbox").json(), client.get(f"/projects/{pid}").json(), client.get(f"/projects/{pid}/events").json())
    assert after == before
    app.dependency_overrides.pop(get_gateway, None)


def test_developer_level_grants_no_permission_or_autonomy(client, session_factory):
    """A developer sees more; it may do nothing more: a manual_learning checkpoint still waits for the owner."""
    uid = _user(client)
    client.patch(f"/users/{uid}/preferences", json={"audience_level": "developer", "code_visibility": "visible"})
    pid = client.post("/projects", json={"owner_id": uid, "title": "P", "goal": "g", "mode": "manual_learning"}).json()["id"]
    resolved_before = client.get("/settings/resolved", params={"project_id": pid}).json()
    t = _ready_task(client, pid, risk="low")
    use_fake(client, session_factory, [CHECKPOINT])
    assert client.post(f"/projects/{pid}/tasks/{t['id']}/checkpoint").json()["auto_decided"] is False
    resolved = client.get("/settings/resolved", params={"project_id": pid}).json()
    assert resolved["interaction_policy"] == "step_by_step"
    assert {k: v for k, v in resolved.items() if k != "models"} == {
        k: v for k, v in resolved_before.items() if k != "models"}  # the preference changed no setting
    app.dependency_overrides.pop(get_gateway, None)


# ---------- the reply language the model is told ----------


def _plan_prompt(client, session_factory, uid, project_layer=None):
    pid = client.post("/projects", json={"owner_id": uid, "title": "P", "goal": "g"}).json()["id"]
    if project_layer:
        client.put(f"/settings/project/{pid}", json=project_layer)
    register_researcher(client)
    fake = use_fake(client, session_factory, [GOOD_PLAN])
    assert client.post(f"/projects/{pid}/plan").status_code == 200
    app.dependency_overrides.pop(get_gateway, None)
    return pid, fake.requests[-1].user


def test_no_language_line_until_the_owner_chose_one(client, session_factory):
    _, prompt = _plan_prompt(client, session_factory, _user(client))
    assert "Reply language" not in prompt  # the default UI language is a proposal, not the owner's answer


def test_reply_language_follows_the_owners_ui_language(client, session_factory):
    uid = _user(client)
    client.patch(f"/users/{uid}/preferences", json={"ui_locale": "tr"})
    _, prompt = _plan_prompt(client, session_factory, uid)
    assert prompt.endswith("Reply language: Turkish (tr)")


def test_output_locale_setting_overrides_the_ui_language(client, session_factory):
    uid = _user(client)
    client.patch(f"/users/{uid}/preferences", json={"ui_locale": "fa"})
    _, prompt = _plan_prompt(client, session_factory, uid, {"output_locale": "en"})
    assert prompt.endswith("Reply language: English (en)")


def test_chat_and_task_prompts_carry_the_reply_language(client, session_factory):
    from tests.test_task_run import RUN_RESULT, use_fake_role

    uid = _user(client)
    client.patch(f"/users/{uid}/preferences", json={"ui_locale": "en"})
    pid = client.post("/projects", json={"owner_id": uid, "title": "P", "goal": "g"}).json()["id"]
    fake = use_fake(client, session_factory, [{"reply": "hi", "suggested_action": "none"}])
    assert client.post(f"/projects/{pid}/chat", json={"text": "سلام"}).status_code == 200
    assert fake.requests[-1].user.endswith("Reply language: English (en)")
    assert "سلام" in fake.requests[-1].user  # the owner's own text is never translated

    register_researcher(client)
    t = _ready_task(client, pid, risk="low", owner="researcher")
    client.post(f"/projects/{pid}/tasks/{t['id']}/decide", json={"option": 0})
    run_fake = use_fake_role(client, session_factory, "research", [RUN_RESULT])
    assert client.post(f"/projects/{pid}/tasks/{t['id']}/run").status_code == 200, "task run"
    assert "Reply language: English (en)" in run_fake.requests[-1].user
    app.dependency_overrides.pop(get_gateway, None)
