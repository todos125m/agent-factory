"""USD per million tokens. Cache writes bill at 1.25x input, cache reads at 0.1x input — Anthropic's
actual cache economics; every provider's cache_read/cache_write tokens are billed at this same ratio
as an approximation (e.g. OpenAI's own cached-input discount has varied by model generation, 50-90%),
so a cache-heavy call's cost is an estimate, not an exact one, for any non-Anthropic provider.

An unlisted model falls back to (0.0, 0.0) — free/no per-call spend (Ollama, the owner's own Claude
subscription) is fine with that; a paid provider's model that isn't listed here silently reports $0
and defeats the budget check, so keep this current when adding a provider whose models bill per token.
"""

PRICES: dict[str, tuple[float, float]] = {
    # model: (input, output)
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
    # OpenAI (verified via web search, Sept 2026 — re-check against the owner's own OpenAI dashboard
    # before relying on this for real spend; prices change and this project has no live pricing feed).
    "gpt-5.5": (5.0, 30.0),
    "gpt-5": (1.25, 10.0),
    "gpt-5-mini": (0.25, 2.0),
    "gpt-5-nano": (0.05, 0.40),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0) -> float:
    price_in, price_out = PRICES.get(model, (0.0, 0.0))
    total = (
        input_tokens * price_in
        + output_tokens * price_out
        + cache_read * price_in * 0.1
        + cache_write * price_in * 1.25
    )
    return round(total / 1_000_000, 6)
