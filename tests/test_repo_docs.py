"""Mocked HTTP layer — no real network calls, no real GitHub API hits (same pattern as
tests/test_sourcing_arbeitnow.py)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from agentic_ai import repo_docs


def _resp(json_data=None, status_code=200, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.text = text
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception("http error")
    else:
        resp.raise_for_status.return_value = None
    return resp


def test_parse_repo_url_bare():
    assert repo_docs.parse_repo_url("github.com/Deepak589/RAG_pipeline") == ("Deepak589", "RAG_pipeline")


def test_parse_repo_url_with_scheme():
    assert repo_docs.parse_repo_url("https://github.com/Deepak589/RAG_pipeline") == ("Deepak589", "RAG_pipeline")
    assert repo_docs.parse_repo_url("http://github.com/Deepak589/RAG_pipeline/") == ("Deepak589", "RAG_pipeline")


ROOT_LISTING = [
    {"name": "README.md", "path": "README.md", "type": "file", "sha": "sha-readme-1",
     "download_url": "https://raw/README.md"},
    {"name": "setup.py", "path": "setup.py", "type": "file", "sha": "sha-setup",
     "download_url": "https://raw/setup.py"},
]
DOCS_LISTING = [
    {"name": "notes.md", "path": "docs/notes.md", "type": "file", "sha": "sha-notes-1",
     "download_url": "https://raw/docs/notes.md"},
]


def test_fetch_repo_files_returns_readme_and_docs(tmp_path, monkeypatch):
    monkeypatch.setattr(repo_docs, "CACHE_PATH", tmp_path / "cache.json")

    def fake_get(url, timeout=15):
        if url.endswith("/contents"):
            return _resp(ROOT_LISTING)
        if url.endswith("/contents/docs"):
            return _resp(DOCS_LISTING)
        if url == "https://raw/README.md":
            return _resp(text="# Readme\nbody")
        if url == "https://raw/docs/notes.md":
            return _resp(text="# Notes\nbody")
        raise AssertionError(f"unexpected url {url}")

    with patch("agentic_ai.repo_docs.httpx.get", side_effect=fake_get):
        files = repo_docs.fetch_repo_files("owner", "repo")

    paths = {f["path"] for f in files}
    assert paths == {"README.md", "docs/notes.md"}


def test_fetch_repo_files_tolerates_missing_docs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(repo_docs, "CACHE_PATH", tmp_path / "cache.json")

    def fake_get(url, timeout=15):
        if url.endswith("/contents"):
            return _resp(ROOT_LISTING)
        if url.endswith("/contents/docs"):
            return _resp(status_code=404)
        if url == "https://raw/README.md":
            return _resp(text="# Readme\nbody")
        raise AssertionError(f"unexpected url {url}")

    with patch("agentic_ai.repo_docs.httpx.get", side_effect=fake_get):
        files = repo_docs.fetch_repo_files("owner", "repo")

    assert [f["path"] for f in files] == ["README.md"]


def test_fetch_repo_files_sha_cache_skips_unchanged_content_fetch(tmp_path, monkeypatch):
    monkeypatch.setattr(repo_docs, "CACHE_PATH", tmp_path / "cache.json")
    content_calls = []

    def fake_get(url, timeout=15):
        if url.endswith("/contents"):
            return _resp(ROOT_LISTING)
        if url.endswith("/contents/docs"):
            return _resp(status_code=404)
        if url == "https://raw/README.md":
            content_calls.append(url)
            return _resp(text="# Readme\nbody")
        raise AssertionError(f"unexpected url {url}")

    with patch("agentic_ai.repo_docs.httpx.get", side_effect=fake_get):
        first = repo_docs.fetch_repo_files("owner", "repo")
        second = repo_docs.fetch_repo_files("owner", "repo")

    assert len(content_calls) == 1  # second call reused the cached content
    assert first == second


def test_fetch_repo_files_refetches_when_sha_changed(tmp_path, monkeypatch):
    monkeypatch.setattr(repo_docs, "CACHE_PATH", tmp_path / "cache.json")
    content_calls = []
    listing = list(ROOT_LISTING)

    def fake_get(url, timeout=15):
        if url.endswith("/contents"):
            return _resp(listing)
        if url.endswith("/contents/docs"):
            return _resp(status_code=404)
        if url == "https://raw/README.md":
            content_calls.append(url)
            return _resp(text=f"# Readme v{len(content_calls)}\nbody")
        raise AssertionError(f"unexpected url {url}")

    with patch("agentic_ai.repo_docs.httpx.get", side_effect=fake_get):
        repo_docs.fetch_repo_files("owner", "repo")
        listing[0] = {**listing[0], "sha": "sha-readme-2"}
        repo_docs.fetch_repo_files("owner", "repo")

    assert len(content_calls) == 2


def test_chunk_by_heading_intro_plus_multiple_headings():
    text = "intro text\n\n# Title One\nbody one\n\n## Sub Heading\nbody two\n\n### Deep!\nbody three\n"
    chunks = repo_docs.chunk_by_heading(text, "proj:README.md")

    ids = [c[0] for c in chunks]
    assert ids == [
        "proj:README.md#intro",
        "proj:README.md#title-one",
        "proj:README.md#sub-heading",
        "proj:README.md#deep",
    ]
    assert "intro text" in chunks[0][1]
    assert "body one" in chunks[1][1]
    assert "body two" in chunks[2][1]
    assert "body three" in chunks[3][1]


def test_chunk_by_heading_no_intro_when_first_line_is_heading():
    chunks = repo_docs.chunk_by_heading("# Only Heading\nbody", "p")
    assert [c[0] for c in chunks] == ["p#only-heading"]


def test_chunk_by_heading_skips_whitespace_only_intro():
    chunks = repo_docs.chunk_by_heading("   \n\n# Heading\nbody", "p")
    assert [c[0] for c in chunks] == ["p#heading"]
