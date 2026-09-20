"""Langfuse tracing is opt-in: no keys set -> no-op, no import, no network call."""

from __future__ import annotations

import builtins
import sys

from agentic_ai import graph
from agentic_ai.config import settings


def test_no_op_without_keys_and_never_imports_langfuse(monkeypatch) -> None:
    monkeypatch.setattr(settings, "langfuse_public_key", None)
    monkeypatch.setattr(settings, "langfuse_secret_key", None)

    real_import = builtins.__import__

    def blocking_import(name, *args, **kwargs):
        if name == "langfuse" or name.startswith("langfuse."):
            raise AssertionError("langfuse must not be imported when unconfigured")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocking_import)
    monkeypatch.delitem(sys.modules, "langfuse", raising=False)
    monkeypatch.delitem(sys.modules, "langfuse.langchain", raising=False)

    assert graph._tracing_callbacks() == []


def test_partial_config_is_still_a_no_op(monkeypatch) -> None:
    monkeypatch.setattr(settings, "langfuse_public_key", "pk-test-dummy")
    monkeypatch.setattr(settings, "langfuse_secret_key", None)

    assert graph._tracing_callbacks() == []


def test_callback_returned_when_both_keys_set(monkeypatch) -> None:
    monkeypatch.setattr(settings, "langfuse_public_key", "pk-test-dummy")
    monkeypatch.setattr(settings, "langfuse_secret_key", "sk-test-dummy")
    monkeypatch.setattr(settings, "langfuse_host", "http://localhost:1")

    callbacks = graph._tracing_callbacks()

    assert len(callbacks) == 1
