"""Mode D: OpenAI's own API via the official `openai` SDK. No real API calls — the SDK client is
injected directly (mirrors test_anthropic_provider_builds_cached_request_and_maps_usage's pattern),
so these tests don't need the `openai` package installed except the one test that checks that path.
"""

from types import SimpleNamespace

import pytest

from app.gateway.openai_provider import OpenAIProvider
from app.gateway.providers import ModelRequest, ProviderError

REQUEST = ModelRequest(model="gpt-5.5", system="S", user="U", max_output_tokens=100)


def _provider(create_fn):
    p = OpenAIProvider()
    p._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create_fn)))
    return p


def _completion(content, finish_reason="stop", model="gpt-5.5", prompt_tokens=10, completion_tokens=5,
                 cached_tokens=None):
    details = SimpleNamespace(cached_tokens=cached_tokens) if cached_tokens is not None else None
    return SimpleNamespace(
        model=model,
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish_reason)],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                               prompt_tokens_details=details),
    )


def test_complete_maps_basic_response_and_usage():
    sent = {}

    def create(**kw):
        sent.update(kw)
        return _completion("hi")

    r = _provider(create).complete(REQUEST)
    assert r.text == "hi" and r.model == "gpt-5.5" and r.stop_reason == "stop"
    assert r.usage.input_tokens == 10 and r.usage.output_tokens == 5 and r.usage.cache_read_tokens == 0

    assert sent["model"] == "gpt-5.5"
    assert sent["max_completion_tokens"] == 100
    assert sent["messages"] == [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
    assert "response_format" not in sent


def test_complete_sends_schema_as_response_format_and_parses_data():
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    req = ModelRequest(model="gpt-5.5", system="s", user="u", max_output_tokens=10, json_schema=schema)
    sent = {}

    def create(**kw):
        sent.update(kw)
        return _completion('{"ok": true}')

    r = _provider(create).complete(req)
    assert r.data == {"ok": True}
    assert sent["response_format"] == {"type": "json_schema", "json_schema": {"name": "response", "schema": schema, "strict": False}}


def test_effort_is_sent_as_reasoning_effort():
    req = ModelRequest(model="gpt-5.5", system="s", user="u", max_output_tokens=10, effort="low")
    sent = {}

    def create(**kw):
        sent.update(kw)
        return _completion("hi")

    _provider(create).complete(req)
    assert sent["reasoning_effort"] == "low"


def test_no_effort_means_no_reasoning_effort_kwarg():
    sent = {}

    def create(**kw):
        sent.update(kw)
        return _completion("hi")

    _provider(create).complete(REQUEST)
    assert "reasoning_effort" not in sent


def test_cached_tokens_are_mapped_when_present():
    def create(**kw):
        return _completion("hi", cached_tokens=7)

    r = _provider(create).complete(REQUEST)
    assert r.usage.cache_read_tokens == 7


def test_cached_tokens_are_not_double_counted_in_input_tokens():
    """OpenAI's prompt_tokens already includes the cached subset (unlike Anthropic's input_tokens,
    which excludes it) — cost_usd()'s Anthropic-style formula would double-bill the cached tokens
    if input_tokens still carried the raw total."""
    def create(**kw):
        return _completion("hi", prompt_tokens=2000, cached_tokens=1500)

    r = _provider(create).complete(REQUEST)
    assert r.usage.input_tokens == 500  # 2000 total - 1500 already-cached
    assert r.usage.cache_read_tokens == 1500


def test_truncated_output_reports_max_tokens_not_raw_finish_reason():
    r = _provider(lambda **kw: _completion("cut off", finish_reason="length")).complete(REQUEST)
    assert r.stop_reason == "max_tokens"


def test_content_filter_raises_provider_error():
    def create(**kw):
        return _completion("", finish_reason="content_filter")

    with pytest.raises(ProviderError, match="content filter"):
        _provider(create).complete(REQUEST)


def test_empty_choices_raises_provider_error_not_indexerror():
    def create(**kw):
        return SimpleNamespace(model="gpt-5.5", choices=[], usage=None)

    with pytest.raises(ProviderError, match="no choices"):
        _provider(create).complete(REQUEST)


def test_structured_output_refusal_raises_provider_error():
    """A Structured Outputs safety refusal comes back as message.refusal with content=None, not a
    finish_reason of content_filter — must surface the real reason, not an "invalid JSON" error."""
    def create(**kw):
        return SimpleNamespace(
            model="gpt-5.5",
            choices=[SimpleNamespace(
                message=SimpleNamespace(content=None, refusal="I can't help with that request."),
                finish_reason="stop",
            )],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, prompt_tokens_details=None),
        )

    with pytest.raises(ProviderError, match="I can't help"):
        _provider(create).complete(REQUEST)


def test_truncated_output_skips_json_parse_and_reports_max_tokens():
    req = ModelRequest(model="m", system="s", user="u", max_output_tokens=5, json_schema={"type": "object"})

    def create(**kw):
        return _completion('{"partial": "cut off h', finish_reason="length")

    r = _provider(create).complete(req)
    assert r.stop_reason == "max_tokens" and r.data is None


def test_invalid_json_for_requested_schema_raises_provider_error():
    req = ModelRequest(model="m", system="s", user="u", max_output_tokens=10, json_schema={"type": "object"})

    def create(**kw):
        return _completion("not json")

    with pytest.raises(ProviderError, match="invalid JSON"):
        _provider(create).complete(req)


def test_sdk_exception_is_wrapped_in_provider_error():
    def create(**kw):
        raise RuntimeError("rate limit exceeded")

    with pytest.raises(ProviderError, match="rate limit exceeded"):
        _provider(create).complete(REQUEST)


def test_missing_package_raises_provider_error():
    """The `openai` package is an optional extra and isn't installed in this dev/test env — exercises
    the real lazy-import path, not a mock."""
    with pytest.raises(ProviderError, match="not installed"):
        OpenAIProvider().complete(REQUEST)


def test_openai_is_registered_by_default():
    from app.gateway.providers import default_providers

    assert isinstance(default_providers()["openai"], OpenAIProvider)


# ---------- pricing / budget interaction ----------
# (a paid provider silently priced at $0 defeats CLAUDE.md's "every call is budget-checked".)


def test_openai_models_have_real_prices():
    from app.gateway.pricing import cost_usd

    for model in ("gpt-5.5", "gpt-5", "gpt-5-mini", "gpt-5-nano"):
        assert cost_usd(model, 1_000_000, 1_000_000) > 0, f"{model} has no price entry"


def test_openai_call_is_budget_checked_end_to_end(session):
    """A real (client-injected) OpenAIProvider call must still be blocked by a tiny project budget —
    it must not silently cost $0 like a free provider (Ollama/claude_account) would."""
    from app.gateway.service import BudgetExceeded, Gateway
    from app.models import Project, SettingsLayer, SettingsScope, User

    user = User(email="openai-budget@x.com")
    session.add(user)
    session.flush()
    project = Project(owner_id=user.id, title="p", goal="g", budget=0.000001)
    session.add(project)
    session.add(SettingsLayer(scope=SettingsScope.GLOBAL, scope_id=0, values={
        "models": {"manager": {"provider": "openai", "model": "gpt-5.5"}},
    }))
    session.commit()

    provider = _provider(lambda **kw: _completion("should never be called"))
    gw = Gateway(session, providers={"openai": provider})
    with pytest.raises(BudgetExceeded):
        gw.call("manager", system="s", user="u", project_id=project.id)
