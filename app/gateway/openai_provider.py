"""Mode D: OpenAI's own API (owner decision d7 — multi-provider), via the official `openai` SDK.

Kept in its own file, not app/gateway/providers.py, so the rest of the app never needs the `openai`
package installed unless this provider is actually selected — the import is lazy either way (mirrors
AnthropicProvider), this just keeps the two SDKs' code physically apart.

Verified against openai/openai-python's README/helpers.md (WebFetch, not memory) plus current
community documentation for details that changed after older training data: `max_tokens` is
deprecated in favor of `max_completion_tokens` (required by reasoning models, back-compatible with
the rest); Structured Outputs go through `response_format: {"type": "json_schema", ...}`; and
`reasoning_effort` (values "low"/"medium"/"high", matching this app's own `effort` field) is the
reasoning-model equivalent of Anthropic's `effort`. The "system" role (not the newer "developer"
alias) is used for the stable/cacheable part, since it is the one still guaranteed to work across
older and current chat models alike.

`strict` is deliberately left `False`: OpenAI's strict Structured Outputs mode requires every schema
property to be listed in `required` (optional fields must instead be a nullable type, still required)
and every object to set `additionalProperties: false`, recursively — this app's own Pydantic schemas
(PlanIn, CheckpointOut, TaskRunOut, ChatReplyOut, …) don't satisfy that and would be rejected by the
API with `strict: true`. Non-strict mode is best-effort compliance instead, which fits how every
caller here already treats a gateway response: parse and validate it, and handle a mismatch as a
real failure (a 422, or the benchmark tab's own `schema_valid: false`) rather than assuming the
provider guaranteed the shape.
"""

import json
from typing import Any

from app.gateway.providers import ModelRequest, ModelResponse, ProviderError, Usage


class OpenAIProvider:
    name = "openai"

    def __init__(self) -> None:
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import openai
            except ImportError as e:
                raise ProviderError("openai package not installed: pip install -e '.[openai]'") from e
            self._client = openai.OpenAI()  # OPENAI_API_KEY from env
        return self._client

    def complete(self, request: ModelRequest) -> ModelResponse:
        kwargs: dict[str, Any] = {
            "model": request.model,
            "max_completion_tokens": request.max_output_tokens,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
        }
        if request.json_schema:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": request.json_schema, "strict": False},
            }
        if request.effort:
            kwargs["reasoning_effort"] = request.effort

        try:
            response = self._get_client().chat.completions.create(**kwargs)
        except ProviderError:
            raise
        except Exception as e:  # the openai SDK's own exception hierarchy (auth/rate-limit/API errors)
            raise ProviderError(f"OpenAI error: {e}") from e

        if not response.choices:
            raise ProviderError("OpenAI returned no choices")
        choice = response.choices[0]
        if choice.finish_reason == "content_filter":
            raise ProviderError("model refused the request (content filter)")
        if getattr(choice.message, "refusal", None):
            raise ProviderError(f"model refused the request: {choice.message.refusal}")
        text = choice.message.content or ""
        # Mirror OllamaProvider: normalize to the cross-provider truncation marker so a caller
        # checking stop_reason == "max_tokens" (as providers.py itself does, twice) works the same
        # regardless of which provider ran the call.
        stop_reason = "max_tokens" if choice.finish_reason == "length" else choice.finish_reason

        u = response.usage
        cached = 0
        if u is not None and getattr(u, "prompt_tokens_details", None) is not None:
            cached = u.prompt_tokens_details.cached_tokens or 0
        usage = Usage(
            # OpenAI's prompt_tokens already includes the cached subset (unlike Anthropic's
            # input_tokens, which excludes it) — subtract it so cost_usd() doesn't bill it twice.
            input_tokens=(u.prompt_tokens - cached) if u else 0,
            output_tokens=u.completion_tokens if u else 0,
            cache_read_tokens=cached,
        )

        try:
            data = json.loads(text) if request.json_schema and stop_reason != "max_tokens" else None
        except json.JSONDecodeError as e:
            raise ProviderError(f"OpenAI returned invalid JSON for the requested schema: {text[:500]}") from e

        return ModelResponse(text=text, usage=usage, model=response.model, data=data, stop_reason=stop_reason)
