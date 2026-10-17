"""Suite-wide isolation of the on-disk databases and log.

Nine tests in `test_graph_phase3.py` patched `checkpoint_db_path` into `tmp_path` but left
`jobs_db_path` at its default, so any path through them that reached `persist_run` wrote a
row into the real `data/jobpilot.db`. `test_resume_continues_without_repaying_completed_nodes`
is one such path — it crashes `validate_facts` on purpose — and that is how a job titled
"T" at "C" with the jd_text "crash test jd, quite unique" acquired a row and a run in the
production jobs table.

The row is not inert. `already_processed` keys off `verdict IS NOT NULL` in `runs`, dedupe
keys off `content_hash` in `jobs`, and `jobpilot cost` / `judge-stats` aggregate over both:
a fixture that lands in there can suppress or mis-report a real posting.

Patching the fields on `settings` per test is the wrong level to fix this — it has to be
remembered in every new test, which is exactly what failed. Both layers below are autouse,
so a test opts IN to isolation by existing:

1. `_isolate_writable_paths` repoints every write-only `settings` path into `tmp_path`.
   A test that wants its own path still overrides it afterwards; monkeypatch unwinds in
   reverse order, so the override wins while it is set.
2. `_forbid_sqlite_writes_under_data_dir` is the tripwire for everything layer 1 cannot
   see: a hardcoded path, a stale module-level default captured at import, a sqlite handle
   opened by `aiosqlite` on a worker thread. It wraps `sqlite3.connect` itself, so the
   check does not depend on the caller having gone through `settings`.

Deliberately NOT redirected: `profile_path` and `chroma_path`. Tests read those, and
`profile_path` in particular is the real `master_profile.yaml` that `test_profile.py` and
`test_fact_validator.py` assert against — pointing it at an empty tmp dir would turn a
suite-wide isolation fix into a suite-wide failure.

`_forbid_anthropic_requests` is the same idea applied to the other shared resource a test
can reach by accident: the paid API. The DB leak above was a symptom of one —
`test_resume_continues_without_repaying_completed_nodes` patched `analyze` and `rewrite`
but not `agent_cv_node`, and `use_agent_cv` defaults True, so every `pytest` run made a
real billed request and then crashed into `persist_run`. The guard sits at
`ChatAnthropic._generate` / `._agenerate` rather than at `llm.make_llm`, because
`test_llm.py` legitimately CONSTRUCTS clients to assert on `max_retries` and must keep
working; only sending is forbidden.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn, cast

import pytest

from agentic_ai.config import ROOT, settings

#: Nothing under here may be opened by sqlite during a test run.
_FORBIDDEN_DB_ROOT = (ROOT / "data").resolve()

#: Write-only settings paths. Each is a file the suite has a reason to create and no reason
#: to create inside the repo.
_WRITABLE_PATH_FIELDS = ("jobs_db_path", "checkpoint_db_path", "log_path")


@pytest.fixture(autouse=True)
def _isolate_writable_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sandbox = tmp_path / "jobpilot-state"
    sandbox.mkdir(parents=True, exist_ok=True)
    for field in _WRITABLE_PATH_FIELDS:
        default = getattr(settings, field)
        monkeypatch.setattr(settings, field, sandbox / Path(default).name)


@pytest.fixture(autouse=True)
def _forbid_sqlite_writes_under_data_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    real_connect = sqlite3.connect

    def guarded_connect(database: Any, *args: Any, **kwargs: Any) -> sqlite3.Connection:
        # ":memory:", "file::memory:?cache=shared" and the like are not filesystem paths.
        if isinstance(database, (str, Path)) and not str(database).startswith(":"):
            resolved = Path(database).expanduser().resolve()
            if resolved == _FORBIDDEN_DB_ROOT or _FORBIDDEN_DB_ROOT in resolved.parents:
                raise AssertionError(
                    f"test opened a sqlite database inside the repo data dir: {resolved}\n"
                    "Tests must never touch data/jobpilot.db or data/checkpoints.db — a row "
                    "written there changes already_processed, content-hash dedupe and the "
                    "cost ledger for real jobs. Pass an explicit db_path under tmp_path, or "
                    "let the autouse _isolate_writable_paths fixture in tests/conftest.py "
                    "redirect settings for you."
                )
        return cast("sqlite3.Connection", real_connect(database, *args, **kwargs))

    monkeypatch.setattr(sqlite3, "connect", guarded_connect)


@pytest.fixture(autouse=True)
def _forbid_anthropic_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    from langchain_anthropic import ChatAnthropic

    def guard(method: str) -> Callable[..., NoReturn]:
        def _raise(*_args: object, **_kwargs: object) -> NoReturn:
            raise AssertionError(
                f"a test reached ChatAnthropic.{method} — that is a real, billed request.\n"
                "Stub the node (monkeypatch graph.<node>) or pin the setting that routes "
                "around it (e.g. settings.use_agent_cv) in the test."
            )

        return _raise

    for method in ("_generate", "_agenerate", "_stream", "_astream"):
        if hasattr(ChatAnthropic, method):
            monkeypatch.setattr(ChatAnthropic, method, guard(method))
