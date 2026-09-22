"""BatchClient — Anthropic Batches API for the nightly queue (solution.md step 7).

Batch requests can't use `method="json_schema"` the way live calls do (llm.py) —
`_convert_to_anthropic_output_config_format` is a private langchain_anthropic helper and
there's no confirmation the Batches API even accepts `output_config` today. Tool-forcing
(`method="function_calling"`'s mechanism) is the older, stable, unambiguously-supported
way to get structured output, so batch requests force a single tool built from the
target Pydantic schema via `convert_to_anthropic_tool` instead. This is scoped to batch
calls only — step 5's live-call `json_schema` choice is unchanged.
"""

from __future__ import annotations

import time

import anthropic
from langchain_anthropic.chat_models import convert_to_anthropic_tool
from pydantic import BaseModel

from . import costs


class BatchRequest(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    custom_id: str  # convention: f"{job.id}:extract" / f"{job.id}:diagnose" — fetch_results
    # derives the usage record's `node` from the suffix after the last ":".
    model: str
    system: str | list[dict]
    messages: list[dict]
    schema_: type[BaseModel]
    max_tokens: int


def _node_from_custom_id(custom_id: str) -> str:
    return custom_id.rsplit(":", 1)[-1]


class BatchClient:
    """Thin wrapper over `anthropic.Anthropic().messages.batches`. Anthropic-only by
    design (plan.md §0) — no provider abstraction."""

    def __init__(self, client: anthropic.Anthropic | None = None) -> None:
        self._client = client or anthropic.Anthropic()
        self._by_custom_id: dict[str, BatchRequest] = {}

    def submit(self, requests: list[BatchRequest]) -> str:
        self._by_custom_id = {r.custom_id: r for r in requests}
        batch_requests = []
        for r in requests:
            tool = convert_to_anthropic_tool(r.schema_)
            params = {
                "model": r.model,
                "max_tokens": r.max_tokens,
                "system": r.system,
                "messages": r.messages,
                "tools": [tool],
                "tool_choice": {"type": "tool", "name": tool["name"]},
            }
            batch_requests.append({"custom_id": r.custom_id, "params": params})
        batch = self._client.messages.batches.create(requests=batch_requests)
        return batch.id

    def poll(
        self,
        batch_id: str,
        interval_s: float = 30.0,
        timeout_s: float = 3600.0,
        sleep_fn=time.sleep,
    ) -> None:
        """Blocks until `processing_status == "ended"`. Raises `TimeoutError` past
        `timeout_s` — a batch that never finishes must not hang the caller forever."""
        elapsed = 0.0
        while True:
            status = self._client.messages.batches.retrieve(batch_id).processing_status
            if status == "ended":
                return
            if elapsed >= timeout_s:
                raise TimeoutError(f"batch {batch_id!r} did not finish within {timeout_s}s (status={status!r})")
            sleep_fn(interval_s)
            elapsed += interval_s

    def fetch_results(self, batch_id: str) -> dict[str, dict]:
        """`{custom_id: {"parsed": <schema instance|None>, "usage": <dict|None>, "error": <str|None>}}`.
        Never raises for one bad job — a single errored/canceled/expired entry in a
        50-job batch shouldn't crash the whole fan-back; the caller decides what to do
        with an entry that has `error` set."""
        out: dict[str, dict] = {}
        for item in self._client.messages.batches.results(batch_id):
            custom_id = item.custom_id
            result = item.result
            request = self._by_custom_id.get(custom_id)
            if result.type != "succeeded":
                out[custom_id] = {"parsed": None, "usage": None, "error": f"batch result {result.type}"}
                continue
            if request is None:
                out[custom_id] = {"parsed": None, "usage": None, "error": "unknown custom_id — no schema registered"}
                continue
            message = result.message
            tool_block = next((b for b in message.content if getattr(b, "type", None) == "tool_use"), None)
            if tool_block is None:
                out[custom_id] = {"parsed": None, "usage": None, "error": "no tool_use block in batch response"}
                continue
            parsed = request.schema_(**tool_block.input)
            usage = self._price_usage(message.usage, model=request.model, node=_node_from_custom_id(custom_id))
            out[custom_id] = {"parsed": parsed, "usage": usage, "error": None}
        return out

    @staticmethod
    def _price_usage(usage, model: str, node: str) -> dict:
        """Anthropic's Batches API is documented at 50% of the standard per-token rate
        (base rate only — no cache multiplier here: batch `usage` may not carry
        cache_read/cache_creation fields reliably, so every input token is priced at
        base-rate-at-0.5x. ponytail: conservative simplification, not a bug — if a real
        batch response does carry cache fields, price them the way costs.record_usage
        does for live calls, still at 0.5x the resulting rate.)
        """
        in_rate, out_rate = costs._rate(model)
        input_tokens = usage.input_tokens
        output_tokens = usage.output_tokens
        cost = (input_tokens / 1_000_000 * in_rate + output_tokens / 1_000_000 * out_rate) * 0.5
        return {
            "node": node,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "cost_usd": round(cost, 6),
        }
