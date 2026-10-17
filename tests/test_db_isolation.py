"""The isolation in conftest.py is load-bearing, so it gets tested like anything else.

A test suite that silently stops isolating is worse than one that never did: the pollution
lands in `data/jobpilot.db`, where `already_processed` and content-hash dedupe read it, and
nothing fails. These three tests fail instead.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agentic_ai.config import ROOT, settings
from agentic_ai.db import repo

_DATA_DIR = (ROOT / "data").resolve()


def _under_data_dir(path: Path) -> bool:
    resolved = Path(path).resolve()
    return resolved == _DATA_DIR or _DATA_DIR in resolved.parents


@pytest.mark.parametrize("field", ["jobs_db_path", "checkpoint_db_path", "log_path"])
def test_writable_settings_paths_are_redirected_out_of_the_repo(field: str) -> None:
    """Fails if the autouse redirect in conftest.py is removed or misses a field."""
    path = Path(getattr(settings, field))
    assert not _under_data_dir(path), (
        f"settings.{field} still points inside the repo during tests: {path}. "
        "Restore _isolate_writable_paths in tests/conftest.py."
    )


def test_opening_the_production_db_raises_instead_of_writing() -> None:
    """The tripwire has to bite on a direct connect, not only on settings-derived paths."""
    with pytest.raises(AssertionError, match="inside the repo data dir"):
        sqlite3.connect(ROOT / "data" / "jobpilot.db")


def test_in_memory_and_tmp_databases_still_work(tmp_path: Path) -> None:
    """The guard must not be so broad that it breaks the tests it is protecting."""
    with sqlite3.connect(":memory:") as conn:
        assert conn.execute("select 1").fetchone() == (1,)

    # The real repo entry point, through the redirected settings path.
    repo.init_db()
    assert Path(settings.jobs_db_path).exists()
    with sqlite3.connect(settings.jobs_db_path) as conn:
        tables = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    assert {"jobs", "runs"} <= tables
