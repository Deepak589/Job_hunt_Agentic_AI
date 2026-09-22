"""BatchClient — Anthropic Batches API wrapper (solution.md step 7). A fake `anthropic`
client is injected everywhere — zero real network/API calls."""

from __future__ import annotations

from types import SimpleNamespace

from pydantic import BaseModel

from agentic_ai import costs
from agentic_ai.batch import BatchClient, BatchRequest


class _Schema(BaseModel):
    """emit_schema — a trivial structured-output target for tests."""

    value: str


def _request(custom_id: str, model: str = "claude-haiku-4-5-20251001") -> BatchRequest:
    return BatchRequest(
        custom_id=custom_id,
        model=model,
        system="be terse",
        messages=[{"role": "user", "content": "hi"}],
        schema_=_Schema,
        max_tokens=100,
    )


class _FakeBatches:
    def __init__(self) -> None:
        self.create_calls: list[dict] = []
        self._statuses: list[str] = []
        self._results: list = []

    def create(self, requests: list[dict]):
        self.create_calls.append(requests)
        return SimpleNamespace(id="batch_123")

    def retrieve(self, batch_id: str):
        status = self._statuses.pop(0) if len(self._statuses) > 1 else self._statuses[0]
        return SimpleNamespace(processing_status=status)

    def results(self, batch_id: str):
        return iter(self._results)


def _fake_client(batches: _FakeBatches) -> SimpleNamespace:
    return SimpleNamespace(messages=SimpleNamespace(batches=batches))


def test_submit_builds_tool_forcing_params_shape() -> None:
    batches = _FakeBatches()
    client = BatchClient(client=_fake_client(batches))

    batch_id = client.submit([_request("job1:extract")])

    assert batch_id == "batch_123"
    sent = batches.create_calls[0]
    assert len(sent) == 1
    entry = sent[0]
    assert entry["custom_id"] == "job1:extract"
    params = entry["params"]
    assert params["model"] == "claude-haiku-4-5-20251001"
    assert params["system"] == "be terse"
    assert params["messages"] == [{"role": "user", "content": "hi"}]
    assert params["tools"][0]["name"] == "_Schema"
    assert params["tool_choice"] == {"type": "tool", "name": "_Schema"}


def test_poll_returns_once_status_flips_to_ended() -> None:
    batches = _FakeBatches()
    batches._statuses = ["in_progress", "in_progress", "ended"]
    client = BatchClient(client=_fake_client(batches))

    sleeps = []
    client.poll("batch_123", interval_s=5.0, timeout_s=100.0, sleep_fn=sleeps.append)

    assert sleeps == [5.0, 5.0]  # slept between the two non-terminal checks


def test_poll_raises_timeout_error_if_never_ended() -> None:
    import pytest

    batches = _FakeBatches()
    batches._statuses = ["in_progress"]
    client = BatchClient(client=_fake_client(batches))

    with pytest.raises(TimeoutError):
        client.poll("batch_123", interval_s=10.0, timeout_s=15.0, sleep_fn=lambda s: None)


def _succeeded_result(custom_id: str, value: str, input_tokens: int, output_tokens: int):
    tool_block = SimpleNamespace(type="tool_use", input={"value": value})
    message = SimpleNamespace(
        content=[tool_block],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )
    return SimpleNamespace(custom_id=custom_id, result=SimpleNamespace(type="succeeded", message=message))


def _errored_result(custom_id: str):
    return SimpleNamespace(custom_id=custom_id, result=SimpleNamespace(type="errored"))


def test_fetch_results_parses_mixed_succeeded_and_errored() -> None:
    batches = _FakeBatches()
    batches._results = [
        _succeeded_result("job1:extract", "ok", 1000, 200),
        _errored_result("job2:extract"),
    ]
    client = BatchClient(client=_fake_client(batches))
    client.submit([_request("job1:extract"), _request("job2:extract")])

    results = client.fetch_results("batch_123")

    ok = results["job1:extract"]
    assert ok["error"] is None
    assert ok["parsed"] == _Schema(value="ok")
    assert ok["usage"]["node"] == "extract"
    assert ok["usage"]["input_tokens"] == 1000
    assert ok["usage"]["output_tokens"] == 200

    bad = results["job2:extract"]
    assert bad["parsed"] is None
    assert bad["usage"] is None
    assert bad["error"] == "batch result errored"


def test_batch_pricing_is_exactly_half_the_live_rate() -> None:
    """Anthropic's Batches API prices at 50% of the standard per-token rate — compare
    against costs.PRICE_PER_MTOK directly so this never silently drifts from the live
    rate table. This also covers Task 4's "cost reflects the discount" requirement."""
    batches = _FakeBatches()
    batches._results = [_succeeded_result("job1:diagnose", "ok", 1_000_000, 1_000_000)]
    client = BatchClient(client=_fake_client(batches))
    client.submit([_request("job1:diagnose", model="claude-sonnet-5")])

    usage = client.fetch_results("batch_123")["job1:diagnose"]["usage"]

    in_rate, out_rate = costs.PRICE_PER_MTOK["claude-sonnet-5"]
    live_cost = 1_000_000 / 1_000_000 * in_rate + 1_000_000 / 1_000_000 * out_rate
    assert usage["cost_usd"] == round(live_cost * 0.5, 6)
