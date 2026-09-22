"""repo_docs — README + top-level docs/*.md ingestion for the evidence store (plan.md
§5.3, solution.md step 10). Closes "built X yourself" hard gaps that only a repo's own
docs evidence, not a CV bullet.

GitHub's public REST API, unauthenticated: 60 req/hr. Fine for an occasional personal
`index build`, not for hammering this in a loop or across many repos at once.
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from .config import ROOT

logger = logging.getLogger(__name__)

CACHE_PATH = ROOT / "data" / "repo_docs_cache.json"

_HEADING_RE = re.compile(r"^#{1,6}\s+")
_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


def parse_repo_url(repo_url: str) -> tuple[str, str]:
    """"github.com/Deepak589/RAG_pipeline" (bare, per the real yaml) or an
    https://-prefixed form -> ("Deepak589", "RAG_pipeline")."""
    cleaned = re.sub(r"^https?://", "", repo_url.strip()).rstrip("/")
    owner, repo = cleaned.split("/")[-2:]
    return owner, repo


def load_cache() -> dict[str, dict]:
    if not CACHE_PATH.exists():
        return {}
    return json.loads(CACHE_PATH.read_text())


def save_cache(cache: dict[str, dict]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True))


def _list_dir(owner: str, repo: str, path: str = "") -> list[dict]:
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}".rstrip("/")
    resp = httpx.get(url, timeout=15)
    if resp.status_code == 404:
        return []  # no such dir (e.g. no docs/) — normal, not an error
    resp.raise_for_status()
    return resp.json()


def fetch_repo_files(owner: str, repo: str) -> list[dict]:
    """README (any case) + top-level docs/*.md (not recursive), SHA-cached so an
    unchanged file's content is not re-downloaded.

    Returns [] and logs a warning for a repo that is private/renamed/unreachable —
    one bad source shouldn't kill the whole `build_index()` run (same spirit as
    runner.py's per-company try/except).
    """
    cache = load_cache()
    try:
        root_entries = _list_dir(owner, repo)
        readme = next(
            (e for e in root_entries if e.get("type") == "file" and e["name"].lower() == "readme.md"),
            None,
        )
        docs_entries = [
            e for e in _list_dir(owner, repo, "docs")
            if e.get("type") == "file" and e["name"].lower().endswith(".md")
        ]
    except httpx.HTTPError:
        logger.warning("repo_docs: failed listing %s/%s", owner, repo)
        return []

    out: list[dict] = []
    for entry in ([readme] if readme else []) + docs_entries:
        key = f"{owner}/{repo}/{entry['path']}"
        sha = entry["sha"]
        cached = cache.get(key)
        if cached and cached.get("sha") == sha:
            content = cached["content"]  # unchanged — skip the content fetch entirely
        else:
            try:
                resp = httpx.get(entry["download_url"], timeout=15)
                resp.raise_for_status()
            except httpx.HTTPError:
                logger.warning("repo_docs: failed fetching %s", key)
                continue
            content = resp.text
            cache[key] = {"sha": sha, "content": content}
        out.append({"path": entry["path"], "sha": sha, "content": content})

    save_cache(cache)
    return out


def _slugify(heading_line: str) -> str:
    text = heading_line.lstrip("#").strip().lower()
    slug = _SLUG_STRIP_RE.sub("-", text).strip("-")
    return slug or "section"


def chunk_by_heading(text: str, source_id_prefix: str) -> list[tuple[str, str]]:
    """Split markdown on any-level `#` headings. Each chunk is its heading line plus
    body up to the next heading; text before the first heading becomes one "#intro"
    chunk. Empty/whitespace-only chunks are dropped."""
    chunks: list[tuple[str, list[str]]] = []
    current_id = f"{source_id_prefix}#intro"
    current_lines: list[str] = []

    for line in text.splitlines():
        if _HEADING_RE.match(line):
            if current_lines:
                chunks.append((current_id, current_lines))
            current_id = f"{source_id_prefix}#{_slugify(line)}"
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_lines:
        chunks.append((current_id, current_lines))

    return [
        (cid, joined)
        for cid, lines in chunks
        if (joined := "\n".join(lines).strip())
    ]
