from app.main import app
from app.routers.manager import get_gateway
from tests.test_manager import use_fake

CHAT_REPLY = {"reply": "برنامه‌ات آماده نیست؛ بریزیمش؟", "suggested_action": "plan"}


def test_chat_round_trip(client, session_factory, project):
    pid = project["id"]
    use_fake(client, session_factory, [CHAT_REPLY])

    r = client.post(f"/projects/{pid}/chat", json={"text": "چیکار باید بکنم؟"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["role"] == "user" and body["user"]["text"] == "چیکار باید بکنم؟"
    assert body["manager"]["role"] == "manager" and body["manager"]["suggested_action"] == "plan"

    messages = client.get(f"/projects/{pid}/chat").json()
    assert [m["role"] for m in messages] == ["user", "manager"]
    app.dependency_overrides.pop(get_gateway, None)


def test_chat_invalid_suggested_action_rejected(client, session_factory, project):
    pid = project["id"]
    use_fake(client, session_factory, [{"reply": "ok", "suggested_action": "do_it_now"}])

    r = client.post(f"/projects/{pid}/chat", json={"text": "?"})
    assert r.status_code == 422
    app.dependency_overrides.pop(get_gateway, None)


def test_chat_budget_exceeded_returns_402(client, session_factory):
    user = client.post("/users", json={"email": "c@example.com"}).json()
    project = client.post(
        "/projects", json={"owner_id": user["id"], "title": "P", "goal": "g", "budget": 0.000001}
    ).json()
    use_fake(client, session_factory, [CHAT_REPLY])

    r = client.post(f"/projects/{project['id']}/chat", json={"text": "?"})
    assert r.status_code == 402
    app.dependency_overrides.pop(get_gateway, None)


def test_chat_prompt_uses_only_chat_reply_skill(client, session_factory, project):
    pid = project["id"]
    fake = use_fake(client, session_factory, [CHAT_REPLY])
    client.post(f"/projects/{pid}/chat", json={"text": "hi"})
    prompt = fake.requests[0].system
    assert "Skill: chat-reply" in prompt
    assert "Skill: plan-goal" not in prompt and "Skill: decision-checkpoint" not in prompt
    app.dependency_overrides.pop(get_gateway, None)
