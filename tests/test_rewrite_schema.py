"""Bugs.md #1 — rewrite could never emit bullets.

Anthropic's structured outputs force `additionalProperties: false` on every object, so a
`dict[str, ...]` field collapses to `{"properties": {}}` and the model can only answer `{}`.
`rewrite` returned 0 bullets on every run until its schema became a flat list (`DraftOut`).
These tests keep any LLM-facing schema from regressing, and pin the DraftOut -> Draft bridge.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agentic_ai.cv_extract import (
    EducationList,
    ExperienceList,
    Identity,
    LanguagesList,
    ProjectList,
    SkillsList,
)
from agentic_ai.nodes.analyze import AnalysisOut
from agentic_ai.nodes.rewrite import _to_capped_draft
from agentic_ai.profile import Profile
from agentic_ai.section_order import RoleClassification
from agentic_ai.state import DraftBullet, DraftOut

try:
    from anthropic import transform_schema
except ImportError:  # pragma: no cover
    transform_schema = None

needs_transform = pytest.mark.skipif(transform_schema is None, reason="anthropic.transform_schema unavailable")

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "agentic_ai"

# Every class passed to make_structured(...). test_every_make_structured_schema_is_listed
# fails if a new call site is added without extending this.
SCHEMAS = {
    "DraftOut": DraftOut,
    "AnalysisOut": AnalysisOut,
    "RoleClassification": RoleClassification,
    "Identity": Identity,
    "LanguagesList": LanguagesList,
    "EducationList": EducationList,
    "SkillsList": SkillsList,
    "ExperienceList": ExperienceList,
    "ProjectList": ProjectList,
}


def _empty_objects(node: object, path: str = "$") -> list[str]:
    """Paths of objects the API would force to `{}`: no properties, additionalProperties false."""
    bad: list[str] = []
    if isinstance(node, dict):
        if node.get("type") == "object" and not node.get("properties") and node.get("additionalProperties") is False:
            bad.append(path)
        for key, value in node.items():
            bad += _empty_objects(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            bad += _empty_objects(value, f"{path}[{i}]")
    return bad


@needs_transform
@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_structured_schema_has_no_object_the_api_collapses_to_empty(name: str) -> None:
    assert _empty_objects(transform_schema(SCHEMAS[name])) == []


@needs_transform
def test_the_checker_catches_the_old_dict_shaped_draft() -> None:
    """Negative control: the schema that shipped the bug must be flagged."""
    from pydantic import BaseModel

    class OldDraft(BaseModel):
        bullets: dict[str, list[DraftBullet]]

    assert _empty_objects(transform_schema(OldDraft)) != []


@needs_transform
def test_draft_out_bullets_is_a_list_of_fully_specified_objects() -> None:
    bullets = transform_schema(DraftOut)["properties"]["bullets"]
    assert bullets["type"] == "array"
    item = bullets["items"]
    if "$ref" in item:  # transform_schema may keep a $ref into $defs
        item = transform_schema(DraftOut)["$defs"][item["$ref"].split("/")[-1]]
    assert {"text", "metric", "source_bullet_id"} <= set(item["properties"])


def test_every_make_structured_schema_is_listed() -> None:
    used: set[str] = set()
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            called = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
            if called == "make_structured" and len(node.args) >= 2 and isinstance(node.args[1], ast.Name):
                used.add(node.args[1].id)
    assert used - set(SCHEMAS) == set(), "add the new schema to SCHEMAS so it is checked"


# --- DraftOut -> Draft ------------------------------------------------------------------

@pytest.fixture(scope="module")
def profile() -> Profile:
    # The committed example, not the private master_profile.yaml.
    return Profile.load(ROOT / "data" / "master_profile.example.yaml")


def _out(*items: tuple[str, str]) -> DraftOut:
    return DraftOut(
        profile_line="line",
        section_order=["ignored"],
        bullets=[DraftBullet(text=t, source_bullet_id=i) for t, i in items],
        cover_letter="letter",
        highlighted_projects=["proj.example_classifier"],
    )


def test_bullets_are_grouped_by_the_profile_not_the_model(profile: Profile) -> None:
    out = _out(("a", "proj.example_classifier.b1"), ("b", "exp.example_co.b1"), ("c", "proj.example_classifier.b1"))
    draft = _to_capped_draft(out, profile, ["projects", "experience"])
    assert [b.text for b in draft.bullets["experience"]] == ["b"]
    assert [b.text for b in draft.bullets["projects"]] == ["a", "c"]  # order preserved


def test_section_order_and_other_fields_come_through(profile: Profile) -> None:
    draft = _to_capped_draft(_out(("a", "exp.example_co.b1")), profile, ["experience", "skills"])
    assert draft.section_order == ["experience", "skills"]  # the node's order, not the model's
    assert (draft.profile_line, draft.cover_letter) == ("line", "letter")
    assert draft.highlighted_projects == ["proj.example_classifier"]


def test_unknown_source_id_is_kept_so_validate_facts_can_report_it(profile: Profile) -> None:
    draft = _to_capped_draft(_out(("a", "proj.does_not_exist.b9")), profile, ["projects"])
    assert [b.source_bullet_id for b in draft.bullets["projects"]] == ["proj.does_not_exist.b9"]


def test_no_bullets_fails_loudly(profile: Profile) -> None:
    with pytest.raises(RuntimeError, match="no bullets"):
        _to_capped_draft(_out(), profile, ["projects"])
