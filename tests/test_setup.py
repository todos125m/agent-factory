"""The Setup page's data (app/setup.py): what this server has for reaching a model, with no secret in it, and the admin's
one tiny test call. FakeProvider only; nothing leaves the machine."""

import json

import pytest

from app import setup
from app.gateway.providers import FakeProvider, ProviderError
from app.main import app
from app.models import ModelCall
from app.routers.manager import get_gateway
from tests.test_factory_build import fake_models
from tests.test_task_run import use_fake_role  # noqa: F401  (imported for its fixtures' side effects only)


@pytest.fixture(autouse=True)
def _open_test_window(monkeypatch):
    monkeypatch.setattr(setup, "_last_test", 0.0)
    yield
    app.dependency_overrides.pop(get_gateway, None)


def test_the_status_says_what_exists_and_never_a_secret(client, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "super-secret-oauth-token-value")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-super-secret-key-value-0123456789")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-secret-0123456789")
    client.post("/users", json={"email": "a@example.com"})
    r = client.get("/setup/status")
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["is_admin"] is True and s["server"]["database"] == "sqlite" and s["keys"]["available"] is True
    assert s["model"]["claude"]["token"] is True and s["model"]["anthropic_key"] is True and s["model"]["openai_key"] is True
    assert s["model"]["access"] in ("claude_account", "api_key", "ollama") and set(s["model"]["roles"]) >= {"manager", "cheap"}
    for secret in ("super-secret-oauth-token-value", "sk-ant-super-secret", "sk-openai-secret", "test-only-master-secret"):
        assert secret not in r.text


def test_missing_things_show_as_missing(client, monkeypatch):
    for name in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("AGENT_FACTORY_SECRET_KEY")
    client.post("/users", json={"email": "a@example.com"})
    s = client.get("/setup/status").json()
    assert (s["model"]["claude"]["token"], s["model"]["anthropic_key"]) == (False, False)
    assert s["keys"] == {"available": False, "reason": "secret_key_missing"}


def test_a_saved_connection_counts_for_the_api_key_method(client):
    client.post("/users", json={"email": "a@example.com"})
    client.post("/credentials", json={"name": "k", "provider": "anthropic", "key": "sk-test-0123456789abcdefghijWXYZ",
                                      "request_id": "setupconn0001"})
    assert client.get("/setup/status").json()["model"]["connections"] == ["anthropic"]


def test_ollama_is_checked_only_when_it_is_the_method(client, monkeypatch):
    client.post("/users", json={"email": "a@example.com"})
    assert client.get("/setup/status").json()["model"]["ollama"] is None  # not the method: nothing is contacted
    client.put("/settings/global/0", json={"model_access": "ollama", "models": {"cheap": {"provider": "ollama", "model": "qwen2.5:7b"}}})

    class Tags:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps({"models": [{"name": "llama3.2:latest"}]}).encode()

    monkeypatch.setattr(setup.urllib.request, "urlopen", lambda url, timeout: Tags())
    o = client.get("/setup/status").json()["model"]["ollama"]
    assert o["reachable"] is True and o["models"] == ["llama3.2:latest"] and "qwen2.5:7b" in o["missing"]

    def refuse(url, timeout):
        raise setup.urllib.error.URLError("refused")

    monkeypatch.setattr(setup.urllib.request, "urlopen", refuse)
    o = client.get("/setup/status").json()["model"]["ollama"]
    assert o["reachable"] is False and o["models"] == []


def test_the_model_test_makes_one_logged_call_and_reports_the_reply(client, session_factory):
    client.post("/users", json={"email": "a@example.com"})
    fake = fake_models(client, session_factory, [{"text": "ignored"}])
    fake.replies = ["OK"]  # a plain-text reply
    r = client.post("/setup/test-model")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["role"] == "cheap" and body["provider"] == "fake" and body["reply"] == "OK"
    assert len(fake.requests) == 1 and "single word OK" in fake.requests[0].user
    with session_factory() as s:
        [call] = s.query(ModelCall).all()
        assert call.agent == "setup" and call.ok is True  # through the gateway, logged like any call


def test_a_failing_model_is_reported_not_raised(client, session_factory):
    client.post("/users", json={"email": "a@example.com"})

    class Down(FakeProvider):
        def complete(self, request):
            raise ProviderError("Claude Code CLI not found on PATH")

    fake_models(client, session_factory, [], provider=Down())
    r = client.post("/setup/test-model")
    assert r.status_code == 200 and r.json()["ok"] is False and "not found" in r.json()["error"]


def test_only_the_admin_tests_and_not_in_a_burst(client, session_factory):
    client.post("/users", json={"email": "admin@example.com"})
    fake_models(client, session_factory, [])
    assert client.post("/setup/test-model").status_code == 200
    assert client.post("/setup/test-model").status_code == 429  # a few seconds between tests
    client.post("/users", json={"email": "other@example.com"})  # now acting as a plain account
    assert client.get("/setup/status").json()["is_admin"] is False
    r = client.post("/setup/test-model")
    assert r.status_code == 403
