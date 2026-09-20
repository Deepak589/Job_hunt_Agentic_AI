"""Shared ChatAnthropic factory. langchain_anthropic's ChatAnthropic already forwards
`max_retries` to the underlying anthropic SDK client, which retries 429/5xx with
exponential backoff — no need to hand-roll a tenacity wrapper for what the SDK already
does. This just makes the retry count configurable (`Settings.llm_max_retries`) and
applied consistently across all 6 nodes that construct a ChatAnthropic client.
"""

from __future__ import annotations

from langchain_anthropic import ChatAnthropic

from .config import settings


def make_llm(model: str, **kwargs) -> ChatAnthropic:
    kwargs.setdefault("max_retries", settings.llm_max_retries)
    return ChatAnthropic(model=model, **kwargs)
