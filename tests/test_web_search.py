"""Research specialists get web search, with hard limits (owner directive, 2026-09-28).

Covers: Gateway.call's deterministic tool/permission check and budget clamp, each provider's own
capability (Anthropic server tool, Claude Code CLI built-in tool, Ollama/OpenAI capability error),
the evidence guard that downgrades an unsourced FACT, and the registry wiring end to end.
"""

import json
import subprocess
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.gateway.openai_provider import OpenAIProvider
from app.gateway.providers import (
    AnthropicProvider,
    ClaudeAccountProvider,
    FakeProvider,
    ModelRequest,
    ModelResponse,
    OllamaProvider,
    ProviderError,
    Usage,
    WebSearchConfig,
)
from app.gateway.service import WEB_SEARCH_HARD_CEILING, Gateway
from app.main import app
from app.models import ModelCall, SettingsLayer, SettingsScope
from app.routers.manager import get_gateway
from tests.test_task_run import use_fake_role

# ---------- Gateway: deterministic tool/permission check + budget clamp ----------


def test_gateway_attaches_web_search_when_tool_and_permission_granted(session):
    fake = FakeProvider(replies=[{"ok": True}])
    gw = Gateway(session, providers={"fake": fake})
    gw.call(
        "research", system="s", user="u", agent="researcher",
        tools=["web_search"], permissions={"network": True},
        route={"provider": "fake", "model": "m"},
    )
    assert fake.requests[0].web_search == WebSearchConfig(max_uses=5)  # DEFAULTS budget.max_web_searches


def test_gateway_does_not_attach_web_search_without_the_tool(session):
    fake = FakeProvider(replies=[{"ok": True}])
    gw = Gateway(session, providers={"fake": fake})
    gw.call(
        "research", system="s", user="u", tools=[], permissions={"network": True},
        route={"provider": "fake", "model": "m"},
    )
    assert fake.requests[0].web_search is None


def test_gateway_refuses_web_search_tool_without_network_permission(session):
    fake = FakeProvider(replies=[{"ok": True}])
    gw = Gateway(session, providers={"fake": fake})
    with pytest.raises(ProviderError, match="network"):
        gw.call(
            "research", system="s", user="u", agent="researcher",
            tools=["web_search"], permissions={"network": False},
            route={"provider": "fake", "model": "m"},
        )
    assert fake.requests == []  # refused before ever reaching the provider


def test_gateway_refuses_web_search_tool_with_missing_permissions(session):
    """permissions=None (agent registered with no permissions dict at all) must refuse too, not crash."""
    fake = FakeProvider(replies=[{"ok": True}])
    gw = Gateway(session, providers={"fake": fake})
    with pytest.raises(ProviderError, match="network"):
        gw.call(
            "research", system="s", user="u", agent="researcher",
            tools=["web_search"], permissions=None,
            route={"provider": "fake", "model": "m"},
        )


def test_gateway_clamps_max_web_searches_to_hard_ceiling(session):
    session.add(SettingsLayer(scope=SettingsScope.GLOBAL, scope_id=0, values={"budget": {"max_web_searches": 999}}))
    session.commit()
    fake = FakeProvider(replies=[{"ok": True}])
    gw = Gateway(session, providers={"fake": fake})
    gw.call(
        "research", system="s", user="u", tools=["web_search"], permissions={"network": True},
        route={"provider": "fake", "model": "m"},
    )
    assert fake.requests[0].web_search.max_uses == WEB_SEARCH_HARD_CEILING


def test_gateway_zero_max_web_searches_disables_search_without_erroring(session):
    """budget.max_web_searches=0 is an owner-facing kill switch, distinct from the tool/permission
    check: it must not be floored up to 1 (that would silently run a search the owner just disabled)."""
    session.add(SettingsLayer(scope=SettingsScope.GLOBAL, scope_id=0, values={"budget": {"max_web_searches": 0}}))
    session.commit()
    fake = FakeProvider(replies=[{"ok": True}])
    gw = Gateway(session, providers={"fake": fake})
    gw.call(
        "research", system="s", user="u", tools=["web_search"], permissions={"network": True},
        route={"provider": "fake", "model": "m"},
    )
    assert fake.requests[0].web_search is None


def test_gateway_uses_configured_max_web_searches_under_ceiling(session):
    session.add(SettingsLayer(scope=SettingsScope.GLOBAL, scope_id=0, values={"budget": {"max_web_searches": 2}}))
    session.commit()
    fake = FakeProvider(replies=[{"ok": True}])
    gw = Gateway(session, providers={"fake": fake})
    gw.call(
        "research", system="s", user="u", tools=["web_search"], permissions={"network": True},
        route={"provider": "fake", "model": "m"},
    )
    assert fake.requests[0].web_search.max_uses == 2


@dataclass
class _StubProvider:
    """Returns a fixed web_searches count, unlike FakeProvider — for asserting Gateway logs it."""

    name: str = "stub"
    web_searches: int = 0

    def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(text="ok", usage=Usage(input_tokens=1, output_tokens=1), model=request.model,
                              web_searches=self.web_searches)


def test_gateway_records_web_searches_on_the_model_call(session):
    stub = _StubProvider(web_searches=3)
    gw = Gateway(session, providers={"stub": stub})
    gw.call(
        "research", system="s", user="u", tools=["web_search"], permissions={"network": True},
        route={"provider": "stub", "model": "m"},
    )
    call = session.query(ModelCall).one()
    assert call.web_searches == 3


def test_gateway_web_search_unsupported_provider_raises_clear_error(session):
    """ollama/openai must refuse rather than silently run without search (owner directive)."""
    gw = Gateway(session, providers={"ollama": OllamaProvider()})
    with pytest.raises(ProviderError, match="web search"):
        gw.call(
            "research", system="s", user="u", tools=["web_search"], permissions={"network": True},
            route={"provider": "ollama", "model": "llama3.2"},
        )


# ---------- AnthropicProvider: server tool ----------


def test_anthropic_provider_sends_web_search_tool_and_counts_server_tool_use():
    sent = {}

    class Messages:
        def create(self, **kw):
            sent.update(kw)
            return SimpleNamespace(
                stop_reason="end_turn", model="claude-sonnet-5",
                content=[
                    SimpleNamespace(type="server_tool_use", name="web_search"),
                    SimpleNamespace(type="web_search_tool_result"),
                    SimpleNamespace(type="server_tool_use", name="web_search"),
                    SimpleNamespace(type="text", text="answer, source: https://example.com"),
                ],
                usage=SimpleNamespace(input_tokens=10, output_tokens=5,
                                      cache_read_input_tokens=0, cache_creation_input_tokens=0),
            )

    provider = AnthropicProvider()
    provider._client = SimpleNamespace(messages=Messages())
    req = ModelRequest(model="claude-sonnet-5", system="S", user="U", max_output_tokens=100,
                       web_search=WebSearchConfig(max_uses=3))
    r = provider.complete(req)
    assert sent["tools"] == [{"type": "web_search_20260209", "name": "web_search", "max_uses": 3}]
    assert r.web_searches == 2


def test_anthropic_provider_omits_tools_when_web_search_not_requested():
    sent = {}

    class Messages:
        def create(self, **kw):
            sent.update(kw)
            return SimpleNamespace(
                stop_reason="end_turn", model="claude-sonnet-5",
                content=[SimpleNamespace(type="text", text="hi")],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1,
                                      cache_read_input_tokens=0, cache_creation_input_tokens=0),
            )

    provider = AnthropicProvider()
    provider._client = SimpleNamespace(messages=Messages())
    provider.complete(ModelRequest(model="claude-sonnet-5", system="S", user="U", max_output_tokens=10))
    assert "tools" not in sent


# ---------- ClaudeAccountProvider: the CLI's own built-in tool ----------


def _run(stdout="", returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def test_claude_account_enables_only_websearch_and_scales_max_turns():
    payload = json.dumps({
        "is_error": False, "result": "ok", "usage": {"input_tokens": 1, "output_tokens": 1},
        "modelUsage": {
            "claude-haiku-4-5-20251001": {"webSearchRequests": 2},
            "claude-sonnet-5": {"webSearchRequests": 0},
        },
    })
    req = ModelRequest(model="claude-sonnet-5", system="S", user="U", max_output_tokens=100,
                       web_search=WebSearchConfig(max_uses=4))
    with patch("shutil.which", return_value="/usr/bin/claude"), \
         patch("subprocess.run", return_value=_run(stdout=payload)) as run:
        r = ClaudeAccountProvider().complete(req)

    args = run.call_args.args[0]
    assert args[args.index("--tools") + 1] == "WebSearch"
    assert args[args.index("--allowedTools") + 1] == "WebSearch"
    assert args[args.index("--max-turns") + 1] == "7"  # 3 base + 4 max_uses
    assert r.web_searches == 2  # summed across modelUsage, not the top-level usage block


def test_claude_account_disables_tools_when_web_search_not_requested():
    """Regression: a role completion without web_search must keep the existing tools-off, 3-turn
    behaviour exactly (real researcher run once failed with error_max_turns when tools leaked in)."""
    payload = '{"is_error": false, "result": "ok", "usage": {"input_tokens": 1, "output_tokens": 1}}'
    req = ModelRequest(model="claude-sonnet-5", system="S", user="U", max_output_tokens=100)
    with patch("shutil.which", return_value="/usr/bin/claude"), \
         patch("subprocess.run", return_value=_run(stdout=payload)) as run:
        r = ClaudeAccountProvider().complete(req)

    args = run.call_args.args[0]
    assert args[args.index("--tools") + 1] == ""
    assert "--allowedTools" not in args
    assert args[args.index("--max-turns") + 1] == "3"
    assert r.web_searches == 0
    assert run.call_args.kwargs["stdin"] == subprocess.DEVNULL


# ---------- Ollama / OpenAI: no web capability ----------


def test_ollama_refuses_web_search_with_clear_error():
    req = ModelRequest(model="llama3.2", system="S", user="U", max_output_tokens=10,
                       web_search=WebSearchConfig(max_uses=3))
    with pytest.raises(ProviderError, match="[Ww]eb search"):
        OllamaProvider().complete(req)


def test_openai_refuses_web_search_with_clear_error():
    req = ModelRequest(model="gpt-5.5", system="S", user="U", max_output_tokens=10,
                       web_search=WebSearchConfig(max_uses=3))
    with pytest.raises(ProviderError, match="[Ww]eb search"):
        OpenAIProvider().complete(req)


# ---------- Deterministic evidence guard (app/manager.py) ----------

BASE_RESULT = {"summary": "s", "lesson": "l", "next": "n"}


def _start_researcher_task(client, session_factory, project):
    pid = project["id"]
    t = client.post(f"/projects/{pid}/tasks", json={"title": "T", "owner": "researcher"}).json()
    client.post(f"/projects/{pid}/tasks/{t['id']}/transition", json={"status": "READY"})
    client.post(f"/projects/{pid}/tasks/{t['id']}/transition", json={"status": "RUNNING"})
    return pid, t["id"]


def test_fact_without_url_is_downgraded_to_inference_and_flagged(client, session_factory, project):
    pid, tid = _start_researcher_task(client, session_factory, project)
    result = {**BASE_RESULT, "findings": [{"claim": "X grew 40%", "type": "FACT", "basis": "recalled knowledge"}]}
    use_fake_role(client, session_factory, "research", [result])

    r = client.post(f"/projects/{pid}/tasks/{tid}/run")
    assert r.status_code == 200, r.text
    finding = r.json()["task"]["output"]["findings"][0]
    assert finding["type"] == "INFERENCE"
    assert "downgraded" in finding["basis"]

    events = [e["type"] for e in client.get(f"/projects/{pid}/events").json()]
    assert "evidence.downgraded" in events
    app.dependency_overrides.pop(get_gateway, None)


def test_fact_with_a_trusted_source_url_is_not_downgraded(client, session_factory, project):
    pid, tid = _start_researcher_task(client, session_factory, project)
    result = {**BASE_RESULT, "findings": [
        {"claim": "X grew 40%", "type": "FACT", "basis": "https://www.census.gov/report"},
    ]}
    use_fake_role(client, session_factory, "research", [result])

    r = client.post(f"/projects/{pid}/tasks/{tid}/run")
    assert r.status_code == 200, r.text
    finding = r.json()["task"]["output"]["findings"][0]
    assert finding["type"] == "FACT"
    assert finding["basis"] == "https://www.census.gov/report"

    events = [e["type"] for e in client.get(f"/projects/{pid}/events").json()]
    assert "evidence.downgraded" not in events
    app.dependency_overrides.pop(get_gateway, None)


def test_inference_and_hypothesis_are_never_downgraded(client, session_factory, project):
    pid, tid = _start_researcher_task(client, session_factory, project)
    result = {**BASE_RESULT, "findings": [
        {"claim": "Adoption may rise", "type": "INFERENCE", "basis": "no url, reasoned from context"},
        {"claim": "Users might prefer X", "type": "HYPOTHESIS", "basis": "no url, unverified guess"},
    ]}
    use_fake_role(client, session_factory, "research", [result])

    r = client.post(f"/projects/{pid}/tasks/{tid}/run")
    assert r.status_code == 200, r.text
    findings = r.json()["task"]["output"]["findings"]
    assert [f["type"] for f in findings] == ["INFERENCE", "HYPOTHESIS"]

    events = [e["type"] for e in client.get(f"/projects/{pid}/events").json()]
    assert "evidence.downgraded" not in events
    app.dependency_overrides.pop(get_gateway, None)


# ---------- Registry wiring end to end ----------


@pytest.mark.parametrize("name", ["researcher", "customer", "strategy"])
def test_specialist_is_registered_with_web_search_and_network(client, name):
    agent = client.get(f"/agents/{name}").json()
    assert agent["tools"] == ["web_search"]
    assert agent["permissions"]["network"] is True


def test_product_agent_has_no_web_search_tool(client):
    agent = client.get("/agents/product").json()
    assert agent["tools"] == []
    assert agent["permissions"]["network"] is False


def test_run_task_for_researcher_passes_web_search_through_to_the_request(client, session_factory, project):
    """End-to-end plumbing: registry -> Agent row -> manager.run_task -> Gateway.call -> ModelRequest."""
    pid, tid = _start_researcher_task(client, session_factory, project)
    result = {**BASE_RESULT, "findings": []}
    fake = use_fake_role(client, session_factory, "research", [result])

    r = client.post(f"/projects/{pid}/tasks/{tid}/run")
    assert r.status_code == 200, r.text
    assert fake.requests[0].web_search is not None
    app.dependency_overrides.pop(get_gateway, None)


def test_run_task_for_product_has_no_web_search(client, session_factory, project):
    pid = project["id"]
    t = client.post(f"/projects/{pid}/tasks", json={"title": "T", "owner": "product"}).json()
    client.post(f"/projects/{pid}/tasks/{t['id']}/transition", json={"status": "READY"})
    client.post(f"/projects/{pid}/tasks/{t['id']}/transition", json={"status": "RUNNING"})
    result = {**BASE_RESULT, "findings": []}
    fake = use_fake_role(client, session_factory, "research", [result])

    r = client.post(f"/projects/{pid}/tasks/{t['id']}/run")
    assert r.status_code == 200, r.text
    assert fake.requests[0].web_search is None
    app.dependency_overrides.pop(get_gateway, None)


def test_model_calls_endpoint_reports_web_searches(client, session_factory, project):
    pid, tid = _start_researcher_task(client, session_factory, project)
    result = {**BASE_RESULT, "findings": []}
    use_fake_role(client, session_factory, "research", [result])
    client.post(f"/projects/{pid}/tasks/{tid}/run")

    calls = client.get(f"/projects/{pid}/model_calls").json()
    assert calls and "web_searches" in calls[0]
    app.dependency_overrides.pop(get_gateway, None)
