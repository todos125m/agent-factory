from datetime import datetime, timezone

import pytest

from app.gateway.providers import FakeProvider, ProviderError
from app.main import app
from app.models import ChatMessage
from app.routers.manager import get_gateway
from tests.test_manager import use_fake
from tests.test_project_pause import _events, _model_calls, use_real_gateway

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


def test_chat_prompt_uses_only_chat_reply_skill(client, session_factory, project):
    pid = project["id"]
    fake = use_fake(client, session_factory, [CHAT_REPLY])
    client.post(f"/projects/{pid}/chat", json={"text": "hi"})
    prompt = fake.requests[0].system
    assert "Skill: chat-reply" in prompt
    assert "Skill: plan-goal" not in prompt and "Skill: decision-checkpoint" not in prompt
    app.dependency_overrides.pop(get_gateway, None)


# ---------- owner decision d19: the owner's message is stored only together with the reply ----------


class FailingProvider(FakeProvider):
    def complete(self, request):
        self.requests.append(request)
        raise ProviderError("model timed out")


@pytest.mark.parametrize("budget, make_provider, status", [
    (0.000001, lambda: FakeProvider(replies=[CHAT_REPLY]), 402),
    (None, FailingProvider, 502),
    (None, lambda: FakeProvider(replies=[{"reply": "ok", "suggested_action": "do_it_now"}]), 422),
], ids=["budget", "provider_error", "invalid_reply"])
def test_a_turn_without_a_reply_stores_nothing(client, monkeypatch, budget, make_provider, status):
    """Nothing reaches the chat, so a resend leaves no unanswered duplicate; the failure itself stays on the
    record. Through the real gateway, on the route's own session, as in production."""
    user = client.post("/users", json={"email": "d19@example.com"}).json()
    pid = client.post("/projects", json={"owner_id": user["id"], "title": "P", "goal": "g", "budget": budget}).json()["id"]
    use_real_gateway(client, monkeypatch, make_provider())

    r = client.post(f"/projects/{pid}/chat", json={"text": "status?"})
    assert r.status_code == status, r.text
    assert client.get(f"/projects/{pid}/chat").json() == []
    calls = client.get(f"/projects/{pid}/model_calls").json()
    if status == 402:  # refused before the call: the refusal is the record
        assert calls == [] and _events(client, pid)[-1]["type"] == "budget.exceeded"
    else:  # the call was made: logged (ok, or with its error) and billed as usual
        assert [c["ok"] for c in calls] == [status == 422]


@pytest.mark.parametrize("raw, detail", [
    ('{"text": "status?\\u0000"}', "chat text must not contain NUL characters"),
    ('{"text": "hi \\ud800"}', "chat text is not valid Unicode (lone surrogate)"),
], ids=["nul", "lone_surrogate"])
def test_text_the_database_cannot_store_is_refused_before_the_call(client, monkeypatch, project, raw, detail):
    """Stored only after the paid call, such text would fail there, after the spend, on every resend."""
    pid = project["id"]
    fake = use_real_gateway(client, monkeypatch, FakeProvider(replies=[CHAT_REPLY]))

    r = client.post(f"/projects/{pid}/chat", content=raw, headers={"Content-Type": "application/json"})
    assert (r.status_code, r.json()["detail"]) == (422, detail)
    assert fake.requests == [] and _model_calls(client, pid) == 0
    assert client.get(f"/projects/{pid}/chat").json() == []


def test_a_resend_after_a_failed_turn_stores_it_once(client, monkeypatch, project):
    pid = project["id"]
    use_real_gateway(client, monkeypatch, FailingProvider())
    assert client.post(f"/projects/{pid}/chat", json={"text": "status?"}).status_code == 502

    fake = use_real_gateway(client, monkeypatch, FakeProvider(replies=[CHAT_REPLY]))
    assert client.post(f"/projects/{pid}/chat", json={"text": "status?"}).status_code == 200
    messages = client.get(f"/projects/{pid}/chat").json()
    assert [(m["role"], m["text"]) for m in messages] == [("user", "status?"), ("manager", CHAT_REPLY["reply"])]
    assert fake.requests[0].user.endswith("Recent chat:\nuser: status?")  # the failed turn left nothing behind
    assert _model_calls(client, pid) == 2


def test_the_prompt_holds_the_last_stored_messages_and_the_new_one(client, session_factory, monkeypatch, project):
    """Token discipline unchanged: 8 messages in all, the new one (not stored before the call) last."""
    pid = project["id"]
    with session_factory() as s:
        s.add_all(ChatMessage(project_id=pid, role=("user", "manager")[i % 2], text=f"m{i}") for i in range(10))
        s.commit()
    fake = use_real_gateway(client, monkeypatch, FakeProvider(replies=[CHAT_REPLY]))

    assert client.post(f"/projects/{pid}/chat", json={"text": "new"}).status_code == 200
    recent = fake.requests[0].user.split("Recent chat:\n", 1)[1].split("\n")
    assert recent == [f"{('user', 'manager')[i % 2]}: m{i}" for i in range(3, 10)] + ["user: new"]
    stored = client.get(f"/projects/{pid}/chat").json()
    assert [(m["role"], m["text"]) for m in stored[-2:]] == [("user", "new"), ("manager", CHAT_REPLY["reply"])]


def test_the_owners_message_keeps_the_time_it_was_sent(client, monkeypatch, project):
    """Stored after the reply, but stamped when it was sent, before the model call (a fixed clock: the
    system clock can be too coarse to tell the two apart)."""
    pid = project["id"]
    order, sent_at = [], datetime(2026, 1, 1, tzinfo=timezone.utc)

    def clock():
        order.append("sent")
        return sent_at

    class OrderedProvider(FakeProvider):
        def complete(self, request):
            order.append("model call")
            return super().complete(request)

    monkeypatch.setattr("app.chat.utcnow", clock)
    use_real_gateway(client, monkeypatch, OrderedProvider(replies=[CHAT_REPLY]))

    body = client.post(f"/projects/{pid}/chat", json={"text": "status?"}).json()
    assert order == ["sent", "model call"]
    assert datetime.fromisoformat(body["user"]["created_at"]) == sent_at
    assert datetime.fromisoformat(body["manager"]["created_at"]) > sent_at
    assert body["user"]["id"] < body["manager"]["id"]
