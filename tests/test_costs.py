"""improvement.md #1 — token usage + $ cost per LLM call."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from agentic_ai.costs import record_usage
from agentic_ai.state import JobState, Job


def _msg(input_tokens: int, output_tokens: int, *, cache_read: int = 0, cache_creation: int = 0) -> AIMessage:
    return AIMessage(
        content="x",
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "input_token_details": {"cache_read": cache_read, "cache_creation": cache_creation},
        },
    )


def test_record_usage_computes_cost_from_the_rate_table() -> None:
    record = record_usage(_msg(1_000_000, 1_000_000), "claude-haiku-4-5-20251001", "extract_requirements")
    assert record == {
        "node": "extract_requirements",
        "model": "claude-haiku-4-5-20251001",
        "input_tokens": 1_000_000,
        "output_tokens": 1_000_000,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "cost_usd": 6.0,  # $1 in + $5 out per 1M
    }


def test_record_usage_prices_cache_read_at_a_tenth_and_write_at_1_25x() -> None:
    # LangChain's input_tokens already includes cache_read + cache_creation.
    record = record_usage(
        _msg(1_000_000, 0, cache_read=1_000_000, cache_creation=0),
        "claude-sonnet-5", "rewrite",
    )
    assert record["cost_usd"] == pytest.approx(0.20)  # 1M cache-read tokens @ $2/1M * 0.1

    record = record_usage(
        _msg(1_000_000, 0, cache_read=0, cache_creation=1_000_000),
        "claude-sonnet-5", "rewrite",
    )
    assert record["cost_usd"] == pytest.approx(2.50)  # 1M cache-write tokens @ $2/1M * 1.25


def test_record_usage_reads_ephemeral_ttl_cache_creation_field() -> None:
    # Live-confirmed 2026-09-18: langchain_anthropic 1.5.6 zeroes `cache_creation`
    # and reports the real write under `ephemeral_5m_input_tokens` instead.
    msg = AIMessage(
        content="x",
        usage_metadata={
            "input_tokens": 1_000_000,
            "output_tokens": 0,
            "total_tokens": 1_000_000,
            "input_token_details": {
                "cache_read": 0,
                "cache_creation": 0,
                "ephemeral_5m_input_tokens": 1_000_000,
                "ephemeral_1h_input_tokens": 0,
            },
        },
    )
    record = record_usage(msg, "claude-sonnet-5", "rewrite")
    assert record["cache_creation_tokens"] == 1_000_000
    assert record["cost_usd"] == pytest.approx(2.50)


def test_record_usage_unknown_model_raises() -> None:
    with pytest.raises(KeyError):
        record_usage(_msg(100, 100), "gpt-4o", "extract_requirements")


def test_record_usage_missing_usage_metadata_is_zero_cost() -> None:
    record = record_usage(AIMessage(content="x"), "claude-sonnet-5", "diagnose")
    assert record["input_tokens"] == 0
    assert record["cost_usd"] == 0.0


def test_job_state_total_cost_usd_sums_llm_calls() -> None:
    job = Job(id="t", source="manual", title="x", jd_text="...")
    state = JobState(
        job=job,
        llm_calls=[
            {"node": "a", "model": "claude-haiku-4-5", "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.01},
            {"node": "b", "model": "claude-sonnet-5", "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.02},
        ],
    )
    assert state.total_cost_usd == 0.03
