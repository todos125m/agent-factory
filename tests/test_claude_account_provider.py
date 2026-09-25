"""Mode B: Claude via the owner's subscription (Claude Code CLI headless mode). No real CLI calls here."""

import subprocess
from unittest.mock import patch

import pytest

from app.gateway.providers import ClaudeAccountProvider, ModelRequest, ProviderError
from app.gateway.service import Gateway
from app.models import ModelCall, Project, SettingsLayer, SettingsScope, User
from app.settings_layers import DEFAULTS, MODEL_ACCESS_PROVIDERS, resolve

REQUEST = ModelRequest(model="claude-sonnet-5", system="S", user="U", max_output_tokens=100)


def _run(stdout="", returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def test_complete_maps_result_and_usage_and_zeroes_cost():
    payload = (
        '{"is_error": false, "subtype": "success", "result": "hi", '
        '"usage": {"input_tokens": 10, "output_tokens": 5, '
        '"cache_read_input_tokens": 90, "cache_creation_input_tokens": 3}}'
    )
    with patch("shutil.which", return_value="/usr/bin/claude"), \
         patch("subprocess.run", return_value=_run(stdout=payload)) as run:
        r = ClaudeAccountProvider().complete(REQUEST)
    assert r.text == "hi" and r.cost_usd == 0.0 and r.stop_reason == "success"
    assert r.usage.input_tokens == 10 and r.usage.cache_read_tokens == 90 and r.usage.cache_write_tokens == 3
    args = run.call_args.args[0]
    assert args[:2] == ["/usr/bin/claude", "-p"] and "--bare" not in args
    assert "--system-prompt" in args and "--output-format" in args


def test_complete_reads_structured_output_when_schema_requested():
    payload = '{"is_error": false, "result": "", "structured_output": {"ok": true}, "usage": {}}'
    req = ModelRequest(model="m", system="s", user="u", max_output_tokens=10, json_schema={"type": "object"})
    with patch("shutil.which", return_value="/usr/bin/claude"), patch("subprocess.run", return_value=_run(stdout=payload)):
        r = ClaudeAccountProvider().complete(req)
    assert r.data == {"ok": True}


def test_missing_usage_defaults_to_zero():
    with patch("shutil.which", return_value="/usr/bin/claude"), \
         patch("subprocess.run", return_value=_run(stdout='{"is_error": false, "result": "x"}')):
        r = ClaudeAccountProvider().complete(REQUEST)
    assert r.usage.input_tokens == 0 and r.usage.output_tokens == 0 and r.cost_usd == 0.0


def test_cli_not_installed_raises_provider_error():
    with patch("shutil.which", return_value=None):
        with pytest.raises(ProviderError, match="not found"):
            ClaudeAccountProvider().complete(REQUEST)


def test_cli_reports_error_raises_provider_error():
    payload = '{"is_error": true, "subtype": "error_during_execution", "result": "not logged in"}'
    with patch("shutil.which", return_value="/usr/bin/claude"), patch("subprocess.run", return_value=_run(stdout=payload)):
        with pytest.raises(ProviderError, match="not logged in"):
            ClaudeAccountProvider().complete(REQUEST)


def test_unparseable_output_raises_provider_error():
    with patch("shutil.which", return_value="/usr/bin/claude"), patch("subprocess.run", return_value=_run(stdout="not json")):
        with pytest.raises(ProviderError, match="unparseable"):
            ClaudeAccountProvider().complete(REQUEST)


def test_timeout_raises_provider_error():
    with patch("shutil.which", return_value="/usr/bin/claude"), \
         patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["claude"], timeout=120)):
        with pytest.raises(ProviderError, match="timed out"):
            ClaudeAccountProvider().complete(REQUEST)


# ---------- settings precedence ----------


def test_model_access_defaults_to_claude_account_for_every_role(session):
    resolved = resolve(session)
    assert resolved["model_access"] == "claude_account"
    for role in DEFAULTS["models"]:
        assert resolved["models"][role]["provider"] == "claude_account"


def test_model_access_api_key_switches_every_role_to_anthropic(session):
    from app.models import SettingsLayer, SettingsScope

    session.add(SettingsLayer(scope=SettingsScope.GLOBAL, scope_id=0, values={"model_access": "api_key"}))
    session.commit()
    resolved = resolve(session)
    for role in DEFAULTS["models"]:
        assert resolved["models"][role]["provider"] == MODEL_ACCESS_PROVIDERS["api_key"] == "anthropic"


def test_explicit_role_provider_overrides_model_access(session):
    from app.models import SettingsLayer, SettingsScope

    session.add(SettingsLayer(scope=SettingsScope.GLOBAL, scope_id=0, values={
        "model_access": "claude_account",
        "models": {"coding": {"provider": "anthropic", "model": "claude-opus-5"}},
    }))
    session.commit()
    resolved = resolve(session)
    assert resolved["models"]["coding"]["provider"] == "anthropic"  # explicit, wins over the convenience switch
    assert resolved["models"]["manager"]["provider"] == "claude_account"  # untouched role follows model_access


def test_malformed_models_layer_does_not_crash_resolve(session):
    """A layer with a bad shape for `models` (bypassing PUT's shallow key check) must not break
    resolution for every other caller — it's just ignored for the model_access rewrite."""
    from app.models import SettingsLayer, SettingsScope

    session.add(SettingsLayer(scope=SettingsScope.GLOBAL, scope_id=0, values={"models": "gpt-5"}))
    session.commit()
    resolved = resolve(session)
    assert resolved["models"] == "gpt-5"  # deep_merge still replaces it; resolve() just doesn't crash on it


def test_malformed_role_entry_is_skipped_by_model_access_rewrite(session):
    from app.models import SettingsLayer, SettingsScope

    session.add(SettingsLayer(scope=SettingsScope.GLOBAL, scope_id=0, values={
        "models": {"manager": "gpt-5", "research": {"provider": "anthropic", "model": "x"}},
    }))
    session.commit()
    resolved = resolve(session)
    assert resolved["models"]["manager"] == "gpt-5"  # not a dict: left alone, not rewritten
    assert resolved["models"]["research"]["provider"] == "anthropic"  # explicit: untouched


# ---------- gateway budget interaction ----------


def test_subscription_call_bypasses_dollar_budget_check(session):
    """A tiny $ budget must not block a claude_account call: its real cost is always $0,
    so the pre-call estimate (which uses API list pricing) would otherwise wrongly reject it."""
    user = User(email="o@x.com")
    session.add(user)
    session.flush()
    project = Project(owner_id=user.id, title="p", goal="g", budget=0.000001)
    session.add(project)
    session.add(SettingsLayer(scope=SettingsScope.GLOBAL, scope_id=0, values={
        "models": {"manager": {"provider": "claude_account", "model": "claude-opus-5"}},
    }))
    session.commit()

    payload = '{"is_error": false, "result": "ok", "usage": {"input_tokens": 1, "output_tokens": 1}}'
    with patch("shutil.which", return_value="/usr/bin/claude"), \
         patch("subprocess.run", return_value=_run(stdout=payload)):
        gw = Gateway(session)
        response = gw.call("manager", system="s", user="u", project_id=project.id)

    assert response.text == "ok"
    call = session.query(ModelCall).one()
    assert call.cost_usd == 0.0 and call.provider == "claude_account"
