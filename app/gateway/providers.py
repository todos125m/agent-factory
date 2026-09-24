"""Model providers behind one interface. Agents never import these directly (§29)."""

import json
import os
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
    providers: dict[str, Provider] = {"anthropic": AnthropicProvider(), "openai": NotConfiguredProvider("openai")}
    if os.getenv("AGENT_FACTORY_FAKE_MODELS"):
        providers["fake"] = FakeProvider()
    return providers
