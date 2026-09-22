"""Procedural memory from review edits (plan.md §21.4, solution.md step 8) — scoped to
bullet-level text changes only. Human-approved by construction: a line only lands here
because the human edited it during `jobpilot review` and then approved/continued.
"""

from __future__ import annotations

from pathlib import Path

from ruamel.yaml import YAML

from .config import ROOT
from .state import Draft

DEFAULT_PATH = ROOT / "data" / "preferences.yaml"

_yaml = YAML(typ="safe")
_yaml.default_flow_style = False


def diff_edited_bullets(old: Draft, new: Draft) -> list[str]:
    """New/changed bullet texts in `new` vs `old`, matched by source_bullet_id (a human
    may reorder bullets, so match by id, not position). profile_line/cover_letter are
    out of scope — free-form prose, not worth diffing here."""
    old_by_id = {b.source_bullet_id: b.text for bullets in old.bullets.values() for b in bullets}
    lines = []
    for bullets in new.bullets.values():
        for b in bullets:
            if old_by_id.get(b.source_bullet_id) != b.text:
                lines.append(b.text)
    return lines


def record_preferences(lines: list[str], path: Path | None = None) -> None:
    """Append+dedupe into data/preferences.yaml. Creates the file if absent."""
    path = path or DEFAULT_PATH
    existing: list[str] = []
    if path.exists():
        data = _yaml.load(path) or {}
        existing = list(data.get("preferences", []))
    for line in lines:
        if line not in existing:
            existing.append(line)
    path.parent.mkdir(parents=True, exist_ok=True)
    _yaml.dump({"preferences": existing}, path)


def load_preferences(path: Path | None = None) -> list[str]:
    """Empty list if the file doesn't exist — a fresh install has none yet."""
    path = path or DEFAULT_PATH
    if not path.exists():
        return []
    data = _yaml.load(path) or {}
    return list(data.get("preferences", []))
