"""Model providers behind one interface. Agents never import these directly (§29)."""

import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class WebSearchConfig:
    """Attached to a ModelRequest by Gateway.call() only after its deterministic tool/permission
    check passes (app/gateway/service.py) — never set directly by an agent or the model."""

    max_uses: int


@dataclass
class ModelRequest:
    model: str
    system: str  # stable part: cached where the provider supports it
    user: str  # volatile part
    max_output_tokens: int
    json_schema: dict[str, Any] | None = None
    effort: str | None = None
    web_search: WebSearchConfig | None = None


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
    duration_ms: int | None = None  # filled in by Gateway.call() after the request completes
    web_searches: int = 0  # number of web searches the provider actually ran for this call


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
        if request.web_search:
            # Server-side tool (current type per docs/claude-api skill, 2026): Anthropic runs the
            # search and injects results within this same call — no client-side loop needed.
            kwargs["tools"] = [{
                "type": "web_search_20260209",
                "name": "web_search",
                "max_uses": request.web_search.max_uses,
            }]

        response = self._get_client().messages.create(**kwargs)
        if response.stop_reason == "refusal":
            raise ProviderError("model refused the request")
        text = "".join(b.text for b in response.content if b.type == "text")
        web_searches = sum(1 for b in response.content if b.type == "server_tool_use" and b.name == "web_search")
        u = response.usage
        usage = Usage(
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_read_tokens=u.cache_read_input_tokens or 0,
            cache_write_tokens=u.cache_creation_input_tokens or 0,
        )
        data = json.loads(text) if request.json_schema and response.stop_reason != "max_tokens" else None
        return ModelResponse(
            text=text, usage=usage, model=response.model, data=data, stop_reason=response.stop_reason,
            web_searches=web_searches,
        )


class ClaudeAccountProvider:
    """Mode B: the owner's own Claude subscription via the Claude Code CLI headless mode, no API key.

    Runs `claude -p --output-format json` with `--safe-mode` (disables CLAUDE.md, skills, hooks,
    MCP servers, custom commands/agents — auth and model selection are unaffected) plus a fresh,
    empty cwd, so nothing from the owner's `~/.claude` or the project directory leaks into a role
    completion. `--bare` would isolate the same things but also stops Claude Code from reading
    OAuth credentials, which is the whole point of this provider, so it's not used here.
    Authentication is whatever `claude` itself resolves: a local `claude login` session, or
    CLAUDE_CODE_OAUTH_TOKEN (`claude setup-token`) in the environment.
    `--permission-mode dontAsk` denies any tool call that would need a person's approval and pins that
    mode over the owner's own `defaultMode` setting. `--permission-prompts none` is not used: it needs
    CLI v2.1.259 or later and older CLIs reject it as an unknown option, which failed every call on
    CLI 2.1.185 (code.claude.com/docs/en/headless).
    """

    name = "claude_account"
    free = True  # subscription usage: no per-call API spend, so budget $-checks don't apply
    timeout_s = 120

    def complete(self, request: ModelRequest) -> ModelResponse:
        binary = shutil.which("claude")
        if binary is None:
            raise ProviderError("Claude Code CLI not found on PATH; install it or set model_access to 'api_key'")

        # Structured output (--json-schema) can take a second turn to emit the schema-valid answer;
        # with tools disabled the only extra turns are those retries, so 3 is a tight upper bound.
        # "1" failed real runs with error_max_turns (num_turns=2). Each web search that the CLI runs
        # (as a haiku sub-agent turn, confirmed against a real run) costs roughly one more turn, so
        # a request with web search gets that budget added on top, bounded by max_uses.
        max_turns = 3 + request.web_search.max_uses if request.web_search else 3
        args = [
            binary, "-p", request.user,
            "--output-format", "json",
            "--system-prompt", request.system,
            "--model", request.model,
            "--max-turns", str(max_turns),
            "--permission-mode", "dontAsk",
            "--safe-mode",
        ]
        if request.web_search:
            # Only the built-in web search tool, explicitly pre-approved so it runs without a human
            # in the loop (dontAsk would otherwise deny it) — nothing else on.
            args += ["--tools", "WebSearch", "--allowedTools", "WebSearch"]
        else:
            # No built-in tools: a role completion is one answer, not an agent loop. With tools on, a
            # model that decides to e.g. web-search spends its only turn on the tool call and the CLI
            # fails with error_max_turns (seen in a real run of the researcher role).
            args += ["--tools", ""]
        if request.effort:
            args += ["--effort", request.effort]
        if request.json_schema:
            args += ["--json-schema", json.dumps(request.json_schema)]

        with tempfile.TemporaryDirectory(prefix="agent-factory-claude-account-") as cwd:
            try:
                # The CLI writes UTF-8; text=True alone would decode with the OS locale (cp1252 on Windows),
                # garbling Persian replies or failing outright ("ف" is byte 0x81, undefined in cp1252).
                proc = subprocess.run(
                    args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=self.timeout_s, stdin=subprocess.DEVNULL,
                )
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
        # WebSearch runs as a sub-agent turn (confirmed against a real run): the top-level usage
        # block never reports it, but each model entry in modelUsage does, keyed by whichever model
        # ran that sub-agent (typically a cheaper one, not request.model).
        web_searches = sum(
            int(m.get("webSearchRequests") or 0) for m in (payload.get("modelUsage") or {}).values()
        )
        return ModelResponse(
            text=text, usage=usage, model=request.model, data=data,
            stop_reason=payload.get("subtype"), cost_usd=0.0,  # subscription usage: no per-call API spend
            web_searches=web_searches,
        )


class OllamaProvider:
    """Mode C: a local Ollama server (free, no account) via its HTTP API (docs.ollama.com — POST
    /api/chat, stream:false; `format` takes either "json" or a JSON schema object for structured
    output; usage comes back as prompt_eval_count/eval_count). Base URL from OLLAMA_BASE_URL,
    default http://localhost:11434 (the port docker.io/ollama/ollama exposes).
    """

    name = "ollama"
    free = True  # local inference: no per-call spend, so budget $-checks don't apply
    timeout_s = 120

    def complete(self, request: ModelRequest) -> ModelResponse:
        if request.web_search:
            raise ProviderError(
                "Ollama has no web search capability; set model_access to 'claude_account' or 'api_key' "
                "for this role, or remove the 'web_search' tool from the agent"
            )
        # `or` (not just the getenv default) also covers OLLAMA_BASE_URL="" — e.g. .env.example's own
        # unedited line, which os.getenv would otherwise return as-is, breaking the request URL.
        base_url = (os.getenv("OLLAMA_BASE_URL") or "http://localhost:11434").rstrip("/")
        body: dict[str, Any] = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "stream": False,
            "options": {"num_predict": request.max_output_tokens},
        }
        if request.json_schema:
            body["format"] = request.json_schema

        req = urllib.request.Request(
            f"{base_url}/api/chat", data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                raw_bytes = resp.read()
        except urllib.error.HTTPError as e:
            raise ProviderError(f"Ollama error ({e.code}): {e.read()[:500].decode('utf-8', 'replace')}") from e
        except urllib.error.URLError as e:
            raise ProviderError(f"Ollama not reachable at {base_url}: {e.reason}") from e
        except TimeoutError as e:
            raise ProviderError(f"Ollama timed out after {self.timeout_s}s") from e

        try:
            raw = json.loads(raw_bytes)
        except json.JSONDecodeError as e:
            raise ProviderError(f"Ollama returned a non-JSON response: {raw_bytes[:500]!r}") from e

        if raw.get("error"):
            raise ProviderError(f"Ollama error: {raw['error']}")
        # "length" means num_predict cut the answer short — mirror AnthropicProvider's "max_tokens"
        # so callers that skip parsing a truncated response (it can't be valid JSON) behave the same.
        stop_reason = "max_tokens" if raw.get("done_reason") == "length" else ("end_turn" if raw.get("done") else None)
        text = (raw.get("message") or {}).get("content", "")
        try:
            data = json.loads(text) if request.json_schema and stop_reason != "max_tokens" else None
        except json.JSONDecodeError as e:
            raise ProviderError(f"Ollama returned invalid JSON for the requested schema: {text[:500]}") from e

        usage = Usage(input_tokens=raw.get("prompt_eval_count", 0), output_tokens=raw.get("eval_count", 0))
        return ModelResponse(
            text=text, usage=usage, model=raw.get("model", request.model), data=data,
            stop_reason=stop_reason, cost_usd=0.0,  # local: no per-call spend
        )


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
    from app.gateway.openai_provider import OpenAIProvider  # local: avoids a circular import at module load

    providers: dict[str, Provider] = {
        "anthropic": AnthropicProvider(),
        "claude_account": ClaudeAccountProvider(),
        "ollama": OllamaProvider(),
        "openai": OpenAIProvider(),
    }
    if os.getenv("AGENT_FACTORY_FAKE_MODELS"):
        providers["fake"] = FakeProvider()
    return providers
