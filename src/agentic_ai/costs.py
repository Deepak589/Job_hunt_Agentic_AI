"""Token usage + $ cost per LLM call (improvement.md #1 — was unimplemented).

Anthropic's per-1M-token rates (verified live via /claude-api pricing lookup,
cached 2026-06-24). Add a row here before routing any node to a new model —
`record_usage` raises rather than silently pricing at $0.
"""

from __future__ import annotations

import structlog
from langchain_core.messages import AIMessage

logger = structlog.get_logger(__name__)

PRICE_PER_MTOK: dict[str, tuple[float, float]] = {
    # model id prefix -> (input $/1M tokens, output $/1M tokens)
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (2.00, 10.00),
}

# Anthropic prices a cache write at 1.25x the base input rate for the 5-minute
# ephemeral cache, 2.0x for the 1-hour one (solution.md step 5 — diagnose.py/rewrite.py
# now use 1h on their cross-job-reusable profile block), and a cache read at 0.1x
# regardless of which ttl wrote it. Pricing every token at the flat input rate would
# understate the base-rate cost and, worse, hide the whole point of caching: a full
# cache hit should cost ~90% less, not the same.
CACHE_WRITE_MULTIPLIER_5M = 1.25
CACHE_WRITE_MULTIPLIER_1H = 2.00
CACHE_READ_MULTIPLIER = 0.10


def _rate(model: str) -> tuple[float, float]:
    for prefix, rate in PRICE_PER_MTOK.items():
        if model.startswith(prefix):
            return rate
    raise KeyError(f"no price entry for model {model!r} — add it to costs.PRICE_PER_MTOK")


def record_usage(raw_message: AIMessage, model: str, node: str, cache_control_expected: bool = False) -> dict:
    """One usage record from a LangChain AIMessage's `.usage_metadata`.

    LangChain's `input_tokens` already folds cache_read + cache_creation into the
    total (see langchain_anthropic._create_usage_metadata), so the cache portions
    are pulled back out of it here rather than re-added.

    `cache_control_expected=True` (set by `llm.invoke_structured` when the request
    actually included a `cache_control` block) warns when a call that SHOULD have
    hit or written the cache did neither — the prompt prefix drifted below Anthropic's
    minimum cacheable length, or the ttl expired between calls, and the cache is
    silently buying nothing.
    """
    usage = getattr(raw_message, "usage_metadata", None) or {}
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    details = usage.get("input_token_details") or {}
    cache_read = details.get("cache_read") or 0
    # langchain_anthropic reports a 5m/1h-ephemeral write under
    # ephemeral_{5m,1h}_input_tokens and zeroes the generic `cache_creation` key
    # when it does (confirmed live 2026-09-18, langchain_anthropic 1.5.6). Treat a
    # nonzero generic `cache_creation` (older/other code path) as a 5m write, since
    # that ttl was this app's only one before the 1h profile-block breakpoints.
    cache_creation_1h = details.get("ephemeral_1h_input_tokens") or 0
    cache_creation_5m = details.get("ephemeral_5m_input_tokens") or 0
    if not cache_creation_5m and not cache_creation_1h:
        cache_creation_5m = details.get("cache_creation") or 0
    cache_creation = cache_creation_5m + cache_creation_1h
    base_input = input_tokens - cache_read - cache_creation

    if cache_control_expected and cache_read == 0 and cache_creation == 0:
        logger.warning(
            "cache_miss_unexpected",
            node=node,
            model=model,
            detail="cache_control was set but neither cache_read nor cache_creation fired — "
            "prefix likely below the minimum cacheable length",
        )

    in_rate, out_rate = _rate(model)
    cost = (
        base_input / 1_000_000 * in_rate
        + cache_read / 1_000_000 * in_rate * CACHE_READ_MULTIPLIER
        + cache_creation_5m / 1_000_000 * in_rate * CACHE_WRITE_MULTIPLIER_5M
        + cache_creation_1h / 1_000_000 * in_rate * CACHE_WRITE_MULTIPLIER_1H
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
