"""Model providers behind one interface. Agents never import these directly (§29)."""

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ModelRequest:
    model: str
    system: str  # stable part: cached where the provider supports it
    user: str  # volatile part
    max_output_tokens: int
    json_schema: dict[str, Any] | None = None
    effort: str | None = None


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass
class ModelResponse:
    text: str
    usage: Usage
    model: str
    data: Any = None  # parsed JSON when a schema was requested
    stop_reason: str | None = None
    cost_usd: float | None = None  # overrides the gateway's pricing-table estimate when set (e.g. subscription calls)


class ProviderError(RuntimeError):
    pass


class Provider(Protocol):
    name: str

    def complete(self, request: ModelRequest) -> ModelResponse: ...


class AnthropicProvider:
    name = "anthropic"

    def __init__(self) -> None:
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as e:
                raise ProviderError("anthropic package not installed: pip install -e '.[anthropic]'") from e
            self._client = anthropic.Anthropic()  # credentials from env / `ant auth login`
        return self._client

    def complete(self, request: ModelRequest) -> ModelResponse:
        kwargs: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_output_tokens,
            # Stable system prompt first and cached; volatile content goes in the user turn.
            "system": [{"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": request.user}],
        }
        output_config: dict[str, Any] = {}
        if request.effort:
            output_config["effort"] = request.effort
        if request.json_schema:
            output_config["format"] = {"type": "json_schema", "schema": request.json_schema}
        if output_config:
            kwargs["output_config"] = output_config

        response = self._get_client().messages.create(**kwargs)
        if response.stop_reason == "refusal":
            raise ProviderError("model refused the request")
        text = "".join(b.text for b in response.content if b.type == "text")
        u = response.usage
        usage = Usage(
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_read_tokens=u.cache_read_input_tokens or 0,
            cache_write_tokens=u.cache_creation_input_tokens or 0,
        )
        data = json.loads(text) if request.json_schema and response.stop_reason != "max_tokens" else None
        return ModelResponse(text=text, usage=usage, model=response.model, data=data, stop_reason=response.stop_reason)


class ClaudeAccountProvider:
    """Mode B: the owner's own Claude subscription via the Claude Code CLI headless mode, no API key.

    Runs `claude -p --output-format json` with `--safe-mode` (disables CLAUDE.md, skills, hooks,
    MCP servers, custom commands/agents — auth and model selection are unaffected) plus a fresh,
    empty cwd, so nothing from the owner's `~/.claude` or the project directory leaks into a role
    completion. `--bare` would isolate the same things but also stops Claude Code from reading
    OAuth credentials, which is the whole point of this provider, so it's not used here.
    Authentication is whatever `claude` itself resolves: a local `claude login` session, or
    CLAUDE_CODE_OAUTH_TOKEN (`claude setup-token`) in the environment.
    """

    name = "claude_account"
    free = True  # subscription usage: no per-call API spend, so budget $-checks don't apply
    timeout_s = 120

    def complete(self, request: ModelRequest) -> ModelResponse:
        binary = shutil.which("claude")
        if binary is None:
            raise ProviderError("Claude Code CLI not found on PATH; install it or set model_access to 'api_key'")

        args = [
            binary, "-p", request.user,
            "--output-format", "json",
            "--system-prompt", request.system,
            "--model", request.model,
            "--max-turns", "1",
            "--permission-prompts", "none",
            "--safe-mode",
        ]
        if request.effort:
            args += ["--effort", request.effort]
        if request.json_schema:
            args += ["--json-schema", json.dumps(request.json_schema)]

        with tempfile.TemporaryDirectory(prefix="agent-factory-claude-account-") as cwd:
            try:
                proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=self.timeout_s)
            except FileNotFoundError as e:
                raise ProviderError("Claude Code CLI not found; install it or set model_access to 'api_key'") from e
            except subprocess.TimeoutExpired as e:
                raise ProviderError(f"Claude Code CLI timed out after {self.timeout_s}s") from e

        try:
            payload = json.loads(proc.stdout)
        except (json.JSONDecodeError, TypeError) as e:
            raise ProviderError(
                f"Claude Code CLI returned unparseable output (exit {proc.returncode}): {proc.stderr[:500] or proc.stdout[:500]}"
            ) from e

        if payload.get("is_error"):
            raise ProviderError(f"Claude Code CLI error ({payload.get('subtype', 'unknown')}): {payload.get('result', '')[:500]}")

        text = payload.get("result", "")
        data = payload.get("structured_output") if request.json_schema else None
        raw_usage = payload.get("usage") or {}
        usage = Usage(
            input_tokens=raw_usage.get("input_tokens", 0),
            output_tokens=raw_usage.get("output_tokens", 0),
            cache_read_tokens=raw_usage.get("cache_read_input_tokens", 0),
            cache_write_tokens=raw_usage.get("cache_creation_input_tokens", 0),
        )
        return ModelResponse(
            text=text, usage=usage, model=request.model, data=data,
            stop_reason=payload.get("subtype"), cost_usd=0.0,  # subscription usage: no per-call API spend
        )


class NotConfiguredProvider:
    """Placeholder for a provider the owner chose but whose adapter/key is not in place yet."""

    def __init__(self, name: str) -> None:
        self.name = name

    def complete(self, request: ModelRequest) -> ModelResponse:
        raise ProviderError(f"provider '{self.name}' is not configured yet")


@dataclass
class FakeProvider:
    """Deterministic provider for tests and offline development: replays queued replies."""

    name: str = "fake"
    replies: list[Any] = field(default_factory=list)
    requests: list[ModelRequest] = field(default_factory=list)

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        reply = self.replies.pop(0) if self.replies else {"ok": True}
        text = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        usage = Usage(input_tokens=(len(request.system) + len(request.user)) // 4, output_tokens=len(text) // 4)
        data = json.loads(text) if request.json_schema else None
        return ModelResponse(text=text, usage=usage, model=request.model, data=data, stop_reason="end_turn")


def default_providers() -> dict[str, Provider]:
    providers: dict[str, Provider] = {
        "anthropic": AnthropicProvider(),
        "claude_account": ClaudeAccountProvider(),
        "openai": NotConfiguredProvider("openai"),
    }
    if os.getenv("AGENT_FACTORY_FAKE_MODELS"):
        providers["fake"] = FakeProvider()
    return providers
