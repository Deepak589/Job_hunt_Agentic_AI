"""llm.make_llm — shared ChatAnthropic factory. Proves `Settings.llm_max_retries` is
actually applied to the constructed client (and the real anthropic SDK client it wraps),
without making any live API calls."""

from __future__ import annotations

from agentic_ai.config import settings
from agentic_ai.llm import make_llm


def test_make_llm_applies_configured_max_retries() -> None:
    llm = make_llm("claude-sonnet-5", max_tokens=100)
    assert llm.max_retries == settings.llm_max_retries
    # The setting must reach the underlying anthropic SDK client, which is what
    # actually retries 429/5xx with exponential backoff.
    assert llm._client.max_retries == settings.llm_max_retries


def test_make_llm_lets_an_explicit_max_retries_override_the_default() -> None:
    llm = make_llm("claude-sonnet-5", max_tokens=100, max_retries=7)
    assert llm.max_retries == 7
    assert llm._client.max_retries == 7


def test_make_llm_respects_a_changed_setting(monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_max_retries", 5)
    llm = make_llm("claude-sonnet-5", max_tokens=100)
    assert llm.max_retries == 5
