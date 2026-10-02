"""Mode C: a local Ollama server (docs.ollama.com POST /api/chat). No real HTTP calls here —
`urllib.request.urlopen` is mocked throughout.
"""

import json
import urllib.error
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from app.gateway.providers import ModelRequest, OllamaProvider, ProviderError, default_providers
from app.settings_layers import DEFAULTS, MODEL_ACCESS_PROVIDERS, resolve

REQUEST = ModelRequest(model="llama3.2", system="S", user="U", max_output_tokens=100)


@contextmanager
def _response(body: dict):
    class Resp:
        def read(self):
            return json.dumps(body).encode("utf-8")

    yield Resp()


def test_complete_maps_message_and_usage_and_zeroes_cost():
    payload = {"model": "llama3.2", "message": {"role": "assistant", "content": "hi"},
               "done": True, "prompt_eval_count": 10, "eval_count": 5}
    with patch("urllib.request.urlopen", return_value=_response(payload)) as urlopen:
        r = OllamaProvider().complete(REQUEST)
    assert r.text == "hi" and r.cost_usd == 0.0 and r.stop_reason == "end_turn"
    assert r.usage.input_tokens == 10 and r.usage.output_tokens == 5

    req = urlopen.call_args.args[0]
    assert req.full_url == "http://localhost:11434/api/chat"
    body = json.loads(req.data)
    assert body["stream"] is False
    assert body["messages"] == [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
    assert "format" not in body


def test_complete_sends_schema_as_format_and_parses_json_content():
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    req_in = ModelRequest(model="llama3.2", system="s", user="u", max_output_tokens=10, json_schema=schema)
    payload = {"message": {"content": '{"ok": true}'}, "done": True}
    with patch("urllib.request.urlopen", return_value=_response(payload)) as urlopen:
        r = OllamaProvider().complete(req_in)
    assert r.data == {"ok": True}
    sent_body = json.loads(urlopen.call_args.args[0].data)
    assert sent_body["format"] == schema


def test_base_url_from_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.local:9999/")
    payload = {"message": {"content": "hi"}, "done": True}
    with patch("urllib.request.urlopen", return_value=_response(payload)) as urlopen:
        OllamaProvider().complete(REQUEST)
    assert urlopen.call_args.args[0].full_url == "http://ollama.local:9999/api/chat"


def test_empty_base_url_env_falls_back_to_default(monkeypatch):
    """.env.example ships OLLAMA_BASE_URL= (empty, not unset) — must not become a relative URL."""
    monkeypatch.setenv("OLLAMA_BASE_URL", "")
    payload = {"message": {"content": "hi"}, "done": True}
    with patch("urllib.request.urlopen", return_value=_response(payload)) as urlopen:
        OllamaProvider().complete(REQUEST)
    assert urlopen.call_args.args[0].full_url == "http://localhost:11434/api/chat"


def test_max_output_tokens_is_sent_as_num_predict():
    payload = {"message": {"content": "hi"}, "done": True}
    with patch("urllib.request.urlopen", return_value=_response(payload)) as urlopen:
        OllamaProvider().complete(REQUEST)
    body = json.loads(urlopen.call_args.args[0].data)
    assert body["options"]["num_predict"] == REQUEST.max_output_tokens


def test_truncated_output_reports_max_tokens_and_skips_json_parse():
    """done_reason "length" means num_predict cut the answer short; a schema was requested but the
    cut-off text is not valid JSON — the provider must report this, not raise/misreport it as JSON."""
    req_in = ModelRequest(model="m", system="s", user="u", max_output_tokens=5, json_schema={"type": "object"})
    payload = {"message": {"content": '{"partial": "cut off h'}, "done": True, "done_reason": "length"}
    with patch("urllib.request.urlopen", return_value=_response(payload)):
        r = OllamaProvider().complete(req_in)
    assert r.stop_reason == "max_tokens" and r.data is None


def test_message_null_does_not_crash():
    payload = {"message": None, "done": True}
    with patch("urllib.request.urlopen", return_value=_response(payload)):
        r = OllamaProvider().complete(REQUEST)
    assert r.text == ""


def test_non_json_top_level_response_raises_provider_error():
    @contextmanager
    def _raw_response(data: bytes):
        class Resp:
            def read(self):
                return data

        yield Resp()

    with patch("urllib.request.urlopen", return_value=_raw_response(b"<html>bad gateway</html>")):
        with pytest.raises(ProviderError, match="non-JSON"):
            OllamaProvider().complete(REQUEST)


def test_missing_usage_defaults_to_zero():
    payload = {"message": {"content": "hi"}, "done": True}
    with patch("urllib.request.urlopen", return_value=_response(payload)):
        r = OllamaProvider().complete(REQUEST)
    assert r.usage.input_tokens == 0 and r.usage.output_tokens == 0


def test_connection_error_raises_provider_error():
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
        with pytest.raises(ProviderError, match="not reachable"):
            OllamaProvider().complete(REQUEST)


def test_http_error_raises_provider_error():
    err = urllib.error.HTTPError(url="u", code=404, msg="not found", hdrs=None, fp=None)
    with patch.object(err, "read", return_value=b"model not found"):
        with patch("urllib.request.urlopen", side_effect=err):
            with pytest.raises(ProviderError, match="404"):
                OllamaProvider().complete(REQUEST)


def test_timeout_raises_provider_error():
    with patch("urllib.request.urlopen", side_effect=TimeoutError()):
        with pytest.raises(ProviderError, match="timed out"):
            OllamaProvider().complete(REQUEST)


def test_200_response_with_error_field_raises_provider_error():
    payload = {"error": "model 'llama99' not found, try pulling it first"}
    with patch("urllib.request.urlopen", return_value=_response(payload)):
        with pytest.raises(ProviderError, match="not found"):
            OllamaProvider().complete(REQUEST)


def test_invalid_json_for_requested_schema_raises_provider_error():
    req_in = ModelRequest(model="m", system="s", user="u", max_output_tokens=10, json_schema={"type": "object"})
    payload = {"message": {"content": "not json"}, "done": True}
    with patch("urllib.request.urlopen", return_value=_response(payload)):
        with pytest.raises(ProviderError, match="invalid JSON"):
            OllamaProvider().complete(req_in)


def test_ollama_is_registered_by_default():
    assert "ollama" in default_providers()


# ---------- settings precedence ----------


def test_model_access_ollama_switches_every_role(session):
    from app.models import SettingsLayer, SettingsScope

    session.add(SettingsLayer(scope=SettingsScope.GLOBAL, scope_id=0, values={"model_access": "ollama"}))
    session.commit()
    resolved = resolve(session)
    for role in DEFAULTS["models"]:
        assert resolved["models"][role]["provider"] == MODEL_ACCESS_PROVIDERS["ollama"] == "ollama"


def test_model_access_ollama_also_rewrites_model_to_a_local_default(session):
    """Switching to ollama must not leave roles pointed at Claude model IDs Ollama doesn't have."""
    from app.models import SettingsLayer, SettingsScope
    from app.settings_layers import MODEL_ACCESS_DEFAULT_MODEL

    session.add(SettingsLayer(scope=SettingsScope.GLOBAL, scope_id=0, values={"model_access": "ollama"}))
    session.commit()
    resolved = resolve(session)
    for role in DEFAULTS["models"]:
        assert resolved["models"][role]["model"] == MODEL_ACCESS_DEFAULT_MODEL["ollama"] == "llama3.2"


def test_model_access_ollama_does_not_override_an_explicit_role_model(session):
    from app.models import SettingsLayer, SettingsScope

    session.add(SettingsLayer(scope=SettingsScope.GLOBAL, scope_id=0, values={
        "model_access": "ollama",
        "models": {"coding": {"model": "qwen2.5-coder"}},
    }))
    session.commit()
    resolved = resolve(session)
    assert resolved["models"]["coding"]["model"] == "qwen2.5-coder"  # explicit: untouched
    assert resolved["models"]["coding"]["provider"] == "ollama"  # still follows the switch
    assert resolved["models"]["manager"]["model"] == "llama3.2"  # untouched role gets the default


def test_model_access_claude_account_does_not_touch_model_names(session):
    """api_key/claude_account share Claude's model namespace, so their roles' models stay untouched."""
    from app.models import SettingsLayer, SettingsScope

    session.add(SettingsLayer(scope=SettingsScope.GLOBAL, scope_id=0, values={"model_access": "claude_account"}))
    session.commit()
    resolved = resolve(session)
    assert resolved["models"]["manager"]["model"] == DEFAULTS["models"]["manager"]["model"]
