"""USD per million tokens. Cache writes bill at 1.25x input, cache reads at 0.1x input."""

PRICES: dict[str, tuple[float, float]] = {
    # model: (input, output)
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
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
