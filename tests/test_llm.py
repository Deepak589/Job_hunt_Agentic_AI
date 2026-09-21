"""llm.make_llm — shared ChatAnthropic factory. Proves `Settings.llm_max_retries` is
actually applied to the constructed client (and the real anthropic SDK client it wraps),
without making any live API calls."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agentic_ai.config import settings
from agentic_ai.llm import invoke_structured, make_llm


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


class _FakeStructured:
    """Stands in for a `make_structured(...)` runnable — `.invoke()` returns the next
    queued `include_raw=True`-shaped result each call."""

    def __init__(self, results: list[dict]) -> None:
        self._results = list(results)
        self.calls = 0

    def invoke(self, messages) -> dict:
        self.calls += 1
        return self._results[min(self.calls, len(self._results)) - 1]


def _result(*, parsed=None, parsing_error=None, stop_reason="end_turn") -> dict:
    return {
        "raw": AIMessage(content="x", response_metadata={"stop_reason": stop_reason}),
        "parsed": parsed,
        "parsing_error": parsing_error,
    }


def test_invoke_structured_does_not_retry_on_a_clean_parse() -> None:
    llm = _FakeStructured([_result(parsed="ok")])
    parsed, usage = invoke_structured(llm, [("human", "hi")], model="claude-sonnet-5", node="test")
    assert parsed == "ok"
    assert llm.calls == 1
    assert len(usage) == 1


def test_invoke_structured_retries_once_on_a_retryable_stop_reason() -> None:
    """solution.md step 5: retry only when the response was cut off or refused."""
    llm = _FakeStructured([
        _result(parsing_error=ValueError("truncated"), stop_reason="max_tokens"),
        _result(parsed="ok", stop_reason="end_turn"),
    ])
    parsed, usage = invoke_structured(llm, [("human", "hi")], model="claude-sonnet-5", node="test")
    assert parsed == "ok"
    assert llm.calls == 2
    assert len(usage) == 2  # the retried call was still billed


def test_invoke_structured_does_not_retry_a_non_retryable_parse_failure() -> None:
    """A schema-conformant request (method='json_schema') that still fails to parse for
    a reason other than truncation/refusal is a real bug — solution.md step 5 explicitly
    drops the old blind parse-retry for this case."""
    llm = _FakeStructured([_result(parsing_error=ValueError("bad json"), stop_reason="end_turn")])
    with pytest.raises(RuntimeError, match="test failed"):
        invoke_structured(llm, [("human", "hi")], model="claude-sonnet-5", node="test")
    assert llm.calls == 1


def test_invoke_structured_warns_on_unexpected_cache_miss() -> None:
    import structlog

    llm = _FakeStructured([_result(parsed="ok")])
    messages = [
        SystemMessage(content=[{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}]),
        HumanMessage(content="hi"),
    ]
    with structlog.testing.capture_logs() as captured:
        invoke_structured(llm, messages, model="claude-sonnet-5", node="test")
    assert any(e.get("event") == "cache_miss_unexpected" for e in captured)


def test_invoke_structured_does_not_warn_without_cache_control() -> None:
    import structlog

    llm = _FakeStructured([_result(parsed="ok")])
    with structlog.testing.capture_logs() as captured:
        invoke_structured(llm, [("human", "hi")], model="claude-sonnet-5", node="test")
    assert not any(e.get("event") == "cache_miss_unexpected" for e in captured)
