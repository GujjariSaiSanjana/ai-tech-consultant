"""Token usage to USD."""

from __future__ import annotations

from collections.abc import Mapping

from scope.config import Price
from scope.llm.types import TokenUsage


def price_for(model: str, prices: Mapping[str, Price]) -> Price:
    """Look up a model's price. Unknown models raise, so a new model is never silently free."""
    p = prices.get(model)
    if p is None:
        # The API may report a dated snapshot id; fall back to the longest matching prefix.
        matches = [k for k in prices if model.startswith(k)]
        if not matches:
            raise KeyError(f"no price configured for model {model!r}")
        p = prices[max(matches, key=len)]
    return p


def cost_usd(model: str, usage: TokenUsage, prices: Mapping[str, Price]) -> float:
    """Price a call."""
    p = price_for(model, prices)
    total = (
        usage.input_tokens * p.input
        + usage.cache_write_tokens * p.input * p.cache_write_multiplier
        + usage.cache_read_tokens * p.cache_read
        + usage.output_tokens * p.output
    )
    return round(total / 1_000_000, 6)
