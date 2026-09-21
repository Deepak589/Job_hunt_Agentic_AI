"""Shared ChatAnthropic factory. langchain_anthropic's ChatAnthropic already forwards
`max_retries` to the underlying anthropic SDK client, which retries 429/5xx with
exponential backoff — no need to hand-roll a tenacity wrapper for what the SDK already
does. This just makes the retry count configurable (`Settings.llm_max_retries`) and
applied consistently across all 6 nodes that construct a ChatAnthropic client.
"""

from __future__ import annotations

from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.runnables import Runnable
from pydantic import BaseModel

from .config import settings
from .costs import record_usage

# Genuinely retryable: the model got cut off before it could close the JSON (max_tokens)
# or refused outright. A schema-conformant request (method="json_schema") that still
# fails to parse for any other reason is a real bug, not a transient hiccup — retrying
# it blind just pays for the same failure twice (solution.md step 5: "drop parse-retry").
_RETRYABLE_STOP_REASONS = {"max_tokens", "refusal"}


def _has_cache_control(messages: list) -> bool:
    """True if any message block in this call actually set `cache_control` — messages
    are either `BaseMessage`s with list-of-dict content (diagnose/rewrite/hiring_manager/
    recruiter_sim) or plain `(role, str)` tuples (extract_requirements/classify_role/
    review, which don't cache)."""
    for m in messages:
        content = getattr(m, "content", None)
        if content is None and isinstance(m, tuple) and len(m) == 2:
            content = m[1]
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and "cache_control" in block:
                    return True
    return False


def make_llm(model: str, **kwargs) -> ChatAnthropic:
    kwargs.setdefault("max_retries", settings.llm_max_retries)
    return ChatAnthropic(model=model, **kwargs)


def make_structured(model: str, schema: type[BaseModel], **kwargs: Any) -> Runnable:
    """`ChatAnthropic` + Claude's native structured-output mode (solution.md step 5).

    `method="json_schema"` binds `output_config={"format": ...}` on the request
    (verified against langchain_anthropic 1.5.x's `with_structured_output` source) —
    the model is schema-constrained server-side, unlike the default
    `method="function_calling"`, which fakes it via forced tool-calling and leaves
    room for a malformed call. `include_raw=True` always, so nodes can read
    `result["raw"].response_metadata["stop_reason"]` for the retry gate.
    """
    return make_llm(model, **kwargs).with_structured_output(schema, method="json_schema", include_raw=True)


def invoke_structured(
    structured_llm: Runnable, messages: list, *, model: str, node: str, verbose: bool = False
) -> tuple[Any, list[dict]]:
    """Call a `make_structured` runnable, recording usage for every call made (a retried
    call was still billed). Retries once, only when `stop_reason` says the response was
    cut off or refused — see `_RETRYABLE_STOP_REASONS`. Raises `RuntimeError` if the
    final attempt still fails to parse.
    """
    usage: list[dict] = []
    cache_control_expected = _has_cache_control(messages)

    def _call(attempt: int):
        result = structured_llm.invoke(messages)
        usage.append(record_usage(result["raw"], model, node, cache_control_expected=cache_control_expected))
        if verbose:
            print(f"--- {node} raw response (attempt {attempt}) ---")
            print(result["raw"].content)
        return result

    result = _call(1)
    stop_reason = result["raw"].response_metadata.get("stop_reason")

    if result["parsing_error"] and stop_reason in _RETRYABLE_STOP_REASONS:
        result = _call(2)
        stop_reason = result["raw"].response_metadata.get("stop_reason")

    if result["parsing_error"]:
        raise RuntimeError(f"{node} failed: {result['parsing_error']} (stop_reason={stop_reason})")
    return result["parsed"], usage
