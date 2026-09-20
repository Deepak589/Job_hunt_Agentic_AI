"""Token usage + $ cost per LLM call (improvement.md #1 — was unimplemented).

Anthropic's per-1M-token rates (verified live via /claude-api pricing lookup,
cached 2026-06-24). Add a row here before routing any node to a new model —
`record_usage` raises rather than silently pricing at $0.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage

PRICE_PER_MTOK: dict[str, tuple[float, float]] = {
    # model id prefix -> (input $/1M tokens, output $/1M tokens)
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (2.00, 10.00),
}

# Anthropic prices a cache write at 1.25x the base input rate and a cache read at
# 0.1x it (5-minute ephemeral cache — the only kind this app uses). Pricing every
# token at the flat input rate would understate the base-rate cost and, worse, hide
# the whole point of caching: a full cache hit should cost ~90% less, not the same.
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.10


def _rate(model: str) -> tuple[float, float]:
    for prefix, rate in PRICE_PER_MTOK.items():
        if model.startswith(prefix):
            return rate
    raise KeyError(f"no price entry for model {model!r} — add it to costs.PRICE_PER_MTOK")


def record_usage(raw_message: AIMessage, model: str, node: str) -> dict:
    """One usage record from a LangChain AIMessage's `.usage_metadata`.

    LangChain's `input_tokens` already folds cache_read + cache_creation into the
    total (see langchain_anthropic._create_usage_metadata), so the cache portions
    are pulled back out of it here rather than re-added.
    """
    usage = getattr(raw_message, "usage_metadata", None) or {}
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    details = usage.get("input_token_details") or {}
    cache_read = details.get("cache_read") or 0
    # langchain_anthropic reports a 5m/1h-ephemeral write under
    # ephemeral_{5m,1h}_input_tokens and zeroes the generic `cache_creation` key
    # when it does (confirmed live 2026-09-18, langchain_anthropic 1.5.6) — this app
    # only ever requests the default 5-minute ephemeral cache, but sum both so a
    # future 1h `cache_control` doesn't silently go uncounted either.
    cache_creation = details.get("cache_creation") or (
        (details.get("ephemeral_5m_input_tokens") or 0)
        + (details.get("ephemeral_1h_input_tokens") or 0)
    )
    base_input = input_tokens - cache_read - cache_creation

    in_rate, out_rate = _rate(model)
    cost = (
        base_input / 1_000_000 * in_rate
        + cache_read / 1_000_000 * in_rate * CACHE_READ_MULTIPLIER
        + cache_creation / 1_000_000 * in_rate * CACHE_WRITE_MULTIPLIER
        + output_tokens / 1_000_000 * out_rate
    )
    return {
        "node": node,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cache_creation_tokens": cache_creation,
        "cost_usd": round(cost, 6),
    }
