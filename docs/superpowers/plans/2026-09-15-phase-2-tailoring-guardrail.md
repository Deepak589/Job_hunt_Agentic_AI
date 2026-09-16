# Phase 2 — Tailoring + the Guardrail — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the Phase 1 gate (extract→retrieve→score→gate) with the tailoring loop —
`diagnose → rewrite → validate_facts → review`, retrying rewrite up to 2 total attempts —
then render the tailored CV + cover letter as PDF via Typst and compute the deterministic
ATS score, so `jobpilot add` on a PROCEED job ends with a scored, ready-to-send document
pair instead of just a gap report.

**Architecture:** Four new LangGraph nodes attach to the `proceed` branch of the existing
Phase 1 graph. `diagnose` and `rewrite` are Claude Sonnet calls with structured output
(same pattern as Phase 1's `extract_requirements`); `validate_facts` is deterministic,
built and unit-tested *before* the rewriter is trusted (plan.md §16); `review` is a
Claude Sonnet judge that loops back to `rewrite` on a low score. A fabricated bullet that
survives two rewrite attempts routes to a hard skip — the pipeline must never hand a
document with an unverified claim to `render_documents`. Rendering and ATS scoring are
deterministic Python (`typst` PyPI package, `pypdf`).

**Tech Stack:** LangGraph (existing), `langchain-anthropic` + Claude Sonnet 5
(`claude-sonnet-5`) for the three new LLM nodes, `typst` (already a dependency, PyPI
package with a Python `compile()` API — no shell binary needed), `pypdf` (new dependency,
added in Task 9) for PDF-text re-extraction.

**Spec:** `plan.md` §2 (state schema), §3 (node contracts), §6 (scoring / ATS score),
§7 (5-role subgraph, section ordering), §8 (fact validator), §10 (document rendering),
§16 Phase 2 (build order). Job-hunt domain rules (XYZ formula, five-role pipeline,
never-dodge-a-gap cover-letter rule): `/Users/deepakkatukuri/Agentic_ai/CLAUDE.md`.

## Global Constraints

- **`extract_requirements` stays blind to the profile** (plan.md §21.2) — nothing in this
  plan changes that node. `diagnose` and `rewrite` DO see the full profile; that is by
  design, not a regression of the same rule.
- **Fabrication is a gate, not a deduction** (CLAUDE.md, plan.md §6/§8) — any unverified
  number, unevidenced tech, or fabricated citation zeros the run. If `validate_facts`
  still fails after the retry cap, the job routes to a skip that names the offending
  claim — it never reaches `render_documents`.
- **XYZ formula, no fabricated numbers** (CLAUDE.md CV Bullet Style) — every bullet is
  `[X outcome] as measured by [Y metric] by doing [Z method]`; skip Y honestly rather than
  invent one. Enforced at the schema level in `rewrite` and checked deterministically in
  `validate_facts`.
- **Max 2 rewrite attempts** (CLAUDE.md Reviewer agent; plan.md §7.4) — after that, ship
  the best version and state the weakness in plain text, never loop forever. One shared
  counter (`JobState.attempt_count`) covers both the fact-validator retry and the
  review-score retry.
- **Section order is a deterministic dict lookup, never LLM freestyle** (plan.md §7,
  CLAUDE.md Section Order) — a cheap classifier picks the role type; a fixed dict maps
  role type → section order.
- **Model routing**: `diagnose_model` / `rewrite_model` / `review_model` = `claude-sonnet-5`
  (plan.md §12 "judgment, tone, defensibility" tier); `role_classifier_model` =
  `claude-haiku-4-5-20251001` (same dated id Phase 1's `extract_model` already uses —
  verified live against real JDs in this repo's own eval history). One settings field per
  purpose, matching the existing `extract_model` pattern — no YAML routing table yet
  (that is Phase 4 polish per §12; not worth building for 4 call sites).
- **No persistence layer in this phase.** SQLite (`plan.md` §11) is not in the Phase 2
  build order — it lands in Phase 3 alongside `recruiter_sim`/`hiring_manager`. Rendered
  PDFs land on disk (`out/<job_id>/`); nothing is written to a database yet.
- **Match existing style.** Every new LLM node copies the exact shape of
  `src/agentic_ai/nodes/requirements.py`: a narrow Pydantic output schema with
  `Field(description=...)` doing double duty as prompt content, `functools.lru_cache`
  for the prompt file and the model client, a two-attempt retry-then-raise `_extract`-style
  function, and a thin graph-node wrapper. Deterministic modules copy `coverage.py`'s
  style: a documented "why" comment block above anything that looks like it could be a
  simpler rule.

---

## File Structure

```
src/agentic_ai/
  state.py                    MODIFY — Diagnosis, Draft, DraftBullet; JobState fields
  config.py                   MODIFY — 4 new model-id settings, 2 new gate settings
  graph.py                    MODIFY — wire diagnose→rewrite→validate_facts→review→render→score
  section_order.py            CREATE — SECTION_ORDER dict + classify_role() node
  render.py                   CREATE — build_render_data() + render_documents() node
  validators/
    __init__.py                CREATE
    facts.py                   CREATE — validate_facts(), deterministic, test-first
  nodes/
    diagnose.py                CREATE
    rewrite.py                 CREATE
    review.py                  CREATE
  scoring/
    __init__.py                CREATE
    ats.py                      CREATE — gates, points, PDF parseability, ats_score()
prompts/
  diagnose.md                 CREATE
  rewrite.md                  CREATE
  review.md                   CREATE
templates/
  cover_letter.typ            CREATE
tests/
  test_fact_validator.py      CREATE (Task 2, before the rewriter is trusted)
  test_section_order.py       CREATE (Task 3)
  test_render.py              CREATE (Task 8)
  test_ats_score.py           CREATE (Task 9)
  test_graph_phase2.py        CREATE (Task 7, routing logic with stub state)
pyproject.toml                MODIFY — add pypdf
src/agentic_ai/cli.py         MODIFY (Task 10) — print diagnosis/draft/ATS, write PDFs
```

Each new node file is single-responsibility (one LLM call, one schema, one graph-node
function) — the same granularity Phase 1 already established. `validators/facts.py` gets
its own package because `profile.py`'s own docstring already names that path as the
Phase 2 fact-checker location (`"validators/facts.py can assert every number..."`) —
following a convention the codebase already committed to, not inventing a new one.

---

### Task 1: State schema — Diagnosis, Draft, DraftBullet

**Files:**
- Modify: `src/agentic_ai/state.py`
- Test: `tests/test_state_phase2.py`

**Interfaces:**
- Produces: `Diagnosis` (fields: `hard_gaps: list[str]`, `soft_gaps: list[str]`,
  `disqualifiers: list[str]`, `matches: list[str]`, `positioning_mismatch: str | None`),
  `DraftBullet` (fields: `text: str`, `metric: str | None`, `source_bullet_id: str`),
  `Draft` (fields: `profile_line: str`, `section_order: list[str]`,
  `bullets: dict[str, list[DraftBullet]]`, `cover_letter: str`,
  `highlighted_projects: list[str]`), `JobState.diagnosis: Diagnosis | None`,
  `JobState.draft: Draft | None`, `JobState.attempt_count: int`,
  `JobState.validation_errors: list[str]`.

- [ ] **Step 1: Write the failing round-trip test**

```python
# tests/test_state_phase2.py
from __future__ import annotations

from agentic_ai.state import Diagnosis, Draft, DraftBullet, Job, JobState


def _job() -> Job:
    return Job(id="t", source="manual", title="Test Role", jd_text="...")


def test_diagnosis_and_draft_round_trip_through_job_state() -> None:
    diag = Diagnosis(
        hard_gaps=["Rust"],
        soft_gaps=["Kubernetes at scale"],
        disqualifiers=[],
        matches=["Python", "FastAPI"],
        positioning_mismatch=None,
    )
    draft = Draft(
        profile_line="AI/ML Engineer...",
        section_order=["projects", "experience", "skills"],
        bullets={
            "projects": [
                DraftBullet(
                    text="Reduced retrieval latency by 40% by building a hybrid BM25+dense retriever.",
                    metric="40%",
                    source_bullet_id="proj.rag_pipeline.b1",
                )
            ]
        },
        cover_letter="Dear hiring team...",
        highlighted_projects=["proj.rag_pipeline"],
    )
    state = JobState(job=_job(), diagnosis=diag, draft=draft, attempt_count=1)

    dumped = state.model_dump_json()
    restored = JobState.model_validate_json(dumped)

    assert restored.diagnosis is not None and restored.diagnosis.hard_gaps == ["Rust"]
    assert restored.draft is not None
    assert restored.draft.bullets["projects"][0].source_bullet_id == "proj.rag_pipeline.b1"
    assert restored.attempt_count == 1
    assert restored.validation_errors == []


def test_diagnosis_and_draft_default_to_none() -> None:
    """Phase 1 states (a skip, before diagnose ever runs) must still validate."""
    state = JobState(job=_job())
    assert state.diagnosis is None
    assert state.draft is None
    assert state.attempt_count == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_state_phase2.py -v`
Expected: FAIL — `ImportError: cannot import name 'Diagnosis' from 'agentic_ai.state'`

- [ ] **Step 3: Add the models**

Add to `src/agentic_ai/state.py`, after the existing `Job` class and before `Scores`:

```python
class Diagnosis(BaseModel):
    """Output of the `diagnose` node — line-by-line CV vs JD (plan.md §7.1)."""

    hard_gaps: list[str] = []  # named requirement, zero evidence — cannot be rewritten away
    soft_gaps: list[str] = []
    disqualifiers: list[str] = []  # met at the gate already; carried through for the record
    matches: list[str] = []  # what the CV already covers well, with evidence
    positioning_mismatch: str | None = None  # e.g. "AI/ML Engineer header vs a data-analyst JD"


class DraftBullet(BaseModel):
    """One rewritten bullet — the XYZ sentence plus what a validator can check it against.

    `text` is the actual line that reaches the CV: a complete sentence, strong verb first,
    the metric printed inline if there is one. `metric` and `source_bullet_id` are NOT
    rendered — they exist so `validate_facts` can check the metric appears verbatim and
    traces to a real profile bullet, without re-parsing prose to find them.
    """

    text: str
    metric: str | None = None  # None is honest — "no number exists", never filled to look nicer
    source_bullet_id: str  # must be in Profile.bullet_ids — this is what makes validation possible


class Draft(BaseModel):
    """Output of the `rewrite` node (plan.md §7.2)."""

    profile_line: str
    section_order: list[str]  # from section_order.SECTION_ORDER — rewrite doesn't invent this
    bullets: dict[str, list[DraftBullet]]  # section_id -> bullets, in the order to print
    cover_letter: str
    highlighted_projects: list[str] = []  # profile project ids surfaced this application
```

Add fields to `JobState`, after `skip_reason`:

```python
    diagnosis: Diagnosis | None = None
    draft: Draft | None = None
    attempt_count: int = 0  # rewrite calls so far — shared cap across the fact-check and review loops
    validation_errors: list[str] = []  # filled by validate_facts; cleared on a clean rewrite
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_state_phase2.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite to confirm no Phase 1 regression**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all prior tests still pass (68 at time of writing) + 2 new

- [ ] **Step 6: Commit**

```bash
git add src/agentic_ai/state.py tests/test_state_phase2.py
git commit -m "Phase 2: add Diagnosis/Draft/DraftBullet to state schema

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015CT5mSBmUWfoR4aQQDNQiL"
```

---

### Task 2: `validators/facts.py` — the fact validator, test-first

Built and unit-tested **before** the rewriter node exists, per plan.md §16 ("validate_facts
unit tests first, before the rewriter is trusted") — this task has no dependency on Task 3
or later; it only needs Task 1's `Draft`/`DraftBullet` and the existing `Profile`.

**Files:**
- Create: `src/agentic_ai/validators/__init__.py` (empty)
- Create: `src/agentic_ai/validators/facts.py`
- Test: `tests/test_fact_validator.py`

**Interfaces:**
- Consumes: `agentic_ai.state.Draft`, `agentic_ai.profile.Profile`,
  `agentic_ai.profile.NUMERIC_RE`, `agentic_ai.profile.expand_metric_tokens`
- Produces: `validate_facts(draft: Draft, profile: Profile, jd_keywords: list[str] = ()) -> list[str]`

- [ ] **Step 1: Write the failing adversarial tests**

```python
# tests/test_fact_validator.py
"""Adversarial cases for validate_facts — the highest-value guardrail (plan.md §8).

Each test is a lie the rewriter is capable of telling. A false negative here is how a
fabricated number or an invented employer reaches an actual application.
"""

from __future__ import annotations

import pytest

from agentic_ai.profile import Profile
from agentic_ai.state import Draft, DraftBullet
from agentic_ai.validators.facts import validate_facts


@pytest.fixture(scope="module")
def profile() -> Profile:
    return Profile.load()


def _draft(text: str, metric: str | None, source_bullet_id: str, section: str = "projects") -> Draft:
    return Draft(
        profile_line="x",
        section_order=[section],
        bullets={section: [DraftBullet(text=text, metric=metric, source_bullet_id=source_bullet_id)]},
        cover_letter="",
    )


def test_real_bullet_with_real_metric_passes(profile: Profile) -> None:
    b = profile.by_id("proj.rag_pipeline.b1")
    assert b is not None and b.metric
    d = _draft(f"Improved retrieval by {b.metric}.", b.metric, b.id)
    assert validate_facts(d, profile) == []


def test_unknown_source_bullet_id_is_an_error(profile: Profile) -> None:
    d = _draft("Did a thing.", None, "proj.does_not_exist.b99")
    errors = validate_facts(d, profile)
    assert any("unknown source" in e for e in errors)


def test_fabricated_number_not_in_profile_is_an_error(profile: Profile) -> None:
    real = profile.by_id("proj.rag_pipeline.b1")
    assert real is not None
    d = _draft("Reduced latency by 87% using a new cache.", "87%", real.id)
    errors = validate_facts(d, profile)
    assert any("87" in e for e in errors)


def test_inflated_percentage_is_caught_even_with_a_real_prefix(profile: Profile) -> None:
    """The P1 regression class from Phase 1's profile_sync bug, replayed against the
    fact validator: a fabricated number that happens to contain a real substring."""
    real = profile.by_id("proj.rag_pipeline.b1")
    assert real is not None and "0.833" in (real.metric or "")
    d = _draft("Reached 0.8339 recall by tuning the retriever.", "0.8339", real.id)
    errors = validate_facts(d, profile)
    assert any("0.8339" in e for e in errors)


def test_metric_field_none_with_no_number_in_text_passes(profile: Profile) -> None:
    """Skipping Y honestly (CLAUDE.md) must never itself be flagged."""
    real = profile.by_id("proj.cloudnotes.b1")
    assert real is not None
    d = _draft("Built the CI/CD pipeline for CloudNotes.", None, real.id)
    assert validate_facts(d, profile) == []


def test_unevidenced_jd_keyword_planted_in_a_bullet_is_an_error(profile: Profile) -> None:
    """The most likely hallucination (plan.md §8): the model sees a JD term and wants
    to please, so it writes tech that is nowhere in the profile."""
    real = profile.by_id("exp.valuemomentum.b1")
    assert real is not None
    d = _draft("Deployed the fix using Kubernetes.", None, real.id)
    errors = validate_facts(d, profile, jd_keywords=["kubernetes"])
    assert any("kubernetes" in e.lower() for e in errors)


def test_jd_keyword_that_is_genuinely_in_the_profile_is_not_flagged(profile: Profile) -> None:
    real = profile.by_id("proj.rag_pipeline.b1")
    assert real is not None
    assert "python" in profile.all_tech()
    d = _draft("Built the retriever in Python.", None, real.id)
    errors = validate_facts(d, profile, jd_keywords=["python"])
    assert errors == []


def test_unclaimable_german_fluency_in_the_cover_letter_is_an_error(profile: Profile) -> None:
    """never_claim: German professional fluency. Checked wherever draft text appears,
    not just in bullets — a cover letter can lie too."""
    d = Draft(
        profile_line="x",
        section_order=["projects"],
        bullets={},
        cover_letter="I am fluent in German and excited to join your team.",
    )
    errors = validate_facts(d, profile)
    assert any("german" in e.lower() for e in errors)


def test_clean_multi_bullet_draft_passes(profile: Profile) -> None:
    b1 = profile.by_id("proj.rag_pipeline.b1")
    b2 = profile.by_id("proj.cloudnotes.b1")
    assert b1 and b2
    d = Draft(
        profile_line="x",
        section_order=["projects"],
        bullets={
            "projects": [
                DraftBullet(text=f"Improved recall by {b1.metric}.", metric=b1.metric, source_bullet_id=b1.id),
                DraftBullet(text="Built the CloudNotes CI/CD pipeline.", metric=None, source_bullet_id=b2.id),
            ]
        },
        cover_letter="Excited to apply my Python and FastAPI experience.",
    )
    assert validate_facts(d, profile, jd_keywords=["python", "fastapi"]) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `mkdir -p src/agentic_ai/validators && touch src/agentic_ai/validators/__init__.py && .venv/bin/python -m pytest tests/test_fact_validator.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentic_ai.validators.facts'`

- [ ] **Step 3: Write the validator**

```python
# src/agentic_ai/validators/facts.py
"""validate_facts — the highest-value guardrail (plan.md §8). Deterministic, no LLM.

Three checks, each catching a distinct way a rewrite can lie:

1. Every bullet cites a real profile bullet (`source_bullet_id`) — this is what makes the
   other two checks possible, and on its own it also settles the "employer/title/dates not
   in real history" gate from plan.md §6: a bullet cannot claim work at a company that
   isn't the real parent of a real cited bullet.
2. Every numeric token printed in a bullet is in `Profile.all_metrics()` — exact match,
   not substring (Phase 1's P1 bug: a fabricated "87" must not pass because "8" or "0.87"
   is real).
3. A JD keyword planted verbatim in a bullet, that the profile has no evidence for, is the
   single most likely hallucination (the model sees "Kubernetes" in the JD and wants to
   please) — checked against the requirement keywords the caller passes in, not a
   general-purpose tech gazetteer that would need building and maintaining separately.

A fourth check runs over the whole draft (bullets + cover letter): an unclaimable-fluency
language claim, reusing the exact language-fluency logic `coverage.py` already built for
the JD-side gate. Broader `never_claim` enforcement (arbitrary prose claims) is not
attempted here — see the "Still open" note this task leaves in tasks/todo.md.
"""

from __future__ import annotations

import re

from ..coverage import FLUENT_LEVELS, _normalize
from ..profile import NUMERIC_RE, Profile, expand_metric_tokens
from ..state import Draft


def _draft_text(draft: Draft) -> list[tuple[str, str]]:
    """(location, text) for every piece of prose in the draft — bullets and cover letter."""
    out = [(f"{section}[{i}]", b.text) for section, bullets in draft.bullets.items() for i, b in enumerate(bullets)]
    if draft.cover_letter.strip():
        out.append(("cover_letter", draft.cover_letter))
    return out


def _unclaimable_language_error(draft: Draft, profile: Profile) -> list[str]:
    langs = profile.raw.get("constraints", {}).get("languages", [])
    unclaimable = {
        _normalize(lang["name"])
        for lang in langs
        if not any(lvl in str(lang.get("level", "")).lower() for lvl in FLUENT_LEVELS)
    }
    if not unclaimable:
        return []
    errors = []
    for loc, text in _draft_text(draft):
        words = set(_normalize(text).split())
        for lang in unclaimable & words:
            errors.append(f"{loc}: claims fluency in {lang} — never_claim forbids this")
    return errors


def validate_facts(draft: Draft, profile: Profile, jd_keywords: list[str] = ()) -> list[str]:
    errors: list[str] = []
    bullet_ids = profile.bullet_ids
    allowed_metrics = profile.all_metrics()
    allowed_tech = profile.all_tech() | profile.all_stack()

    for section, bullets in draft.bullets.items():
        for b in bullets:
            if b.source_bullet_id not in bullet_ids:
                errors.append(f"{section}: bullet cites unknown source {b.source_bullet_id!r}")

            for num in NUMERIC_RE.findall(b.text):
                if num not in allowed_metrics and num.rstrip("%") not in allowed_metrics:
                    errors.append(f"{section}: unverified number {num!r} in: {b.text[:60]}")

            if b.metric and b.metric not in expand_metric_tokens([b.metric]) & allowed_metrics | {b.metric}:
                pass  # metric field itself is caught by the numeric scan above; no separate check needed

    for kw in jd_keywords:
        k = _normalize(kw)
        if not k or k in allowed_tech:
            continue
        pattern = re.compile(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", re.IGNORECASE)
        for loc, text in _draft_text(draft):
            if pattern.search(text):
                errors.append(f"{loc}: claims unevidenced tech {kw!r} (JD term, not in profile)")

    errors.extend(_unclaimable_language_error(draft, profile))
    return errors
```

Note: the `if b.metric and ...` line above is a no-op placeholder in reasoning only — replace
it with nothing (delete it). The numeric scan already covers `b.metric` because `metric`, if
set, is expected to appear inside `b.text` (that's the contract: metric is "echoed for
validation", not a separate rendered field) — so no extra branch is needed. Final version of
that loop body is just the `source_bullet_id` check plus the numeric scan; drop the dead
`if b.metric` line entirely before running the tests.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_fact_validator.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/agentic_ai/validators tests/test_fact_validator.py
git commit -m "Phase 2: validate_facts — the fact validator, built test-first

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015CT5mSBmUWfoR4aQQDNQiL"
```

---

### Task 3: `section_order.py` — role classifier + deterministic section order

**Files:**
- Create: `src/agentic_ai/section_order.py`
- Modify: `src/agentic_ai/config.py` (add `role_classifier_model` setting)
- Test: `tests/test_section_order.py`

**Interfaces:**
- Produces: `RoleType = Literal["data_scientist", "data_analyst", "ai_engineer", "fullstack_ship"]`,
  `SECTION_ORDER: dict[RoleType, list[str]]`, `classify_role(jd_text: str) -> RoleType`
  (LLM call, Haiku), `section_order_for(role: RoleType) -> list[str]` (pure dict lookup).

- [ ] **Step 1: Write the failing test for the deterministic half**

```python
# tests/test_section_order.py
from __future__ import annotations

from agentic_ai.section_order import SECTION_ORDER, section_order_for


def test_every_role_type_has_a_section_order() -> None:
    for role in ("data_scientist", "data_analyst", "ai_engineer", "fullstack_ship"):
        assert role in SECTION_ORDER
        assert section_order_for(role) == SECTION_ORDER[role]


def test_ai_engineer_leads_with_projects() -> None:
    """CLAUDE.md Section Order: AI Engineer JD -> Projects -> Experience -> Skills."""
    assert section_order_for("ai_engineer")[0] == "projects"


def test_data_analyst_leads_with_experience() -> None:
    assert section_order_for("data_analyst")[0] == "experience"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_section_order.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentic_ai.section_order'`

- [ ] **Step 3: Add `role_classifier_model` to config**

In `src/agentic_ai/config.py`, in the `# --- models (§12 routing) ---` block, add below
`extract_model`:

```python
    role_classifier_model: str = "claude-haiku-4-5-20251001"  # cheap, enum output only
    diagnose_model: str = "claude-sonnet-5"
    rewrite_model: str = "claude-sonnet-5"
    review_model: str = "claude-sonnet-5"

    # --- rewrite loop ---
    # CLAUDE.md Reviewer agent: "Max 2 loops — after that, ship best version and flag
    # remaining weakness in plain text rather than looping forever." One counter shared
    # by the fact-validator retry and the review-score retry.
    max_rewrite_attempts: int = 2
    min_review_score: int = 7  # below this, retry rewrite (if attempts remain)
```

- [ ] **Step 4: Write `section_order.py`**

```python
# src/agentic_ai/section_order.py
"""Role classification + deterministic section order (plan.md §7, CLAUDE.md Section Order).

The LLM only picks WHICH role type a JD is; the section order for that role type is a
fixed dict lookup. Letting the model freestyle the order would make it inconsistent
across applications for the same role type — the thing plan.md §7 explicitly warns against.
"""

from __future__ import annotations

import functools
from typing import Literal

from langchain_anthropic import ChatAnthropic
from pydantic import BaseModel, Field

from .config import settings

RoleType = Literal["data_scientist", "data_analyst", "ai_engineer", "fullstack_ship"]

# CLAUDE.md "Section Order (tailor per role)"
SECTION_ORDER: dict[RoleType, list[str]] = {
    "data_scientist": ["skills", "projects", "experience"],
    "data_analyst": ["experience", "skills", "projects"],
    "ai_engineer": ["projects", "experience", "skills"],
    "fullstack_ship": ["profile", "projects", "experience", "skills"],
}


def section_order_for(role: RoleType) -> list[str]:
    return SECTION_ORDER[role]


class RoleClassification(BaseModel):
    """classify_role — which of the four CLAUDE.md role archetypes this JD is."""

    role: RoleType = Field(
        description=(
            "data_scientist: modeling/analysis depth is the ask. "
            "data_analyst: reporting/BI/SQL-first. "
            "ai_engineer: building AI/agentic products or ML systems in production. "
            "fullstack_ship: general shipping-focused role where AI-tool fluency beats exact stack match."
        )
    )


@functools.lru_cache(maxsize=1)
def _prompt() -> str:
    return (
        "Classify this job description into exactly one role archetype for CV section "
        "ordering. Pick the one whose emphasis matches what the JD rewards, not just "
        "keyword overlap — a title alone is not enough."
    )


@functools.lru_cache(maxsize=1)
def _model():
    llm = ChatAnthropic(model=settings.role_classifier_model, temperature=0, max_tokens=256)
    return llm.with_structured_output(RoleClassification, include_raw=True)


def classify_role(jd_text: str) -> RoleType:
    messages = [
        ("system", _prompt()),
        ("human", f"<job_description>\n{jd_text.strip()}\n</job_description>"),
    ]
    last_error: Exception | None = None
    for _ in (1, 2):
        try:
            result = _model().invoke(messages)
            if result["parsing_error"]:
                raise ValueError(result["parsing_error"])
            return result["parsed"].role
        except Exception as exc:  # noqa: BLE001 — retried once, then falls back
            last_error = exc
    # A classifier failure must not block the pipeline — fall back to the general case
    # rather than raise, unlike extract_requirements (whose output the gate depends on).
    del last_error
    return "fullstack_ship"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_section_order.py -v`
Expected: PASS (3 tests — these exercise only the deterministic dict, not `classify_role`
itself, matching this codebase's existing pattern of not unit-testing live LLM calls;
`classify_role` is exercised by the eval-JD runs in Task 7)

- [ ] **Step 6: Commit**

```bash
git add src/agentic_ai/section_order.py src/agentic_ai/config.py tests/test_section_order.py
git commit -m "Phase 2: role classifier + deterministic SECTION_ORDER

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015CT5mSBmUWfoR4aQQDNQiL"
```

---

### Task 4: `prompts/diagnose.md` + `nodes/diagnose.py`

**Files:**
- Create: `prompts/diagnose.md`
- Create: `src/agentic_ai/nodes/diagnose.py`
- Modify: `src/agentic_ai/graph.py` (add node only — wired into edges in Task 7)

**Interfaces:**
- Consumes: `JobState.job`, `JobState.requirements` (with `.evidence` filled by Phase 1's
  `retrieve_evidence`), `Profile.load()`
- Produces: `diagnose(state: JobState) -> dict` (graph node, returns `{"diagnosis": Diagnosis, "notes": [...]}`)

- [ ] **Step 1: Write the prompt**

```markdown
# prompts/diagnose.md — v1

You are the diagnosis stage of a CV-tailoring pipeline. You compare a candidate's real
background against one job description's stated requirements, line by line, and report
the gap — honestly. Nothing downstream can rewrite away a gap you fail to name here.

You are given:
- the job description
- the requirements already extracted from it, each with the retrieved evidence (profile
  bullets) that best matches it, and whether the deterministic gate already scored it
  covered
- the candidate's full profile (skills, experience, projects, education)

## Your job

1. **Hard gaps** — a hard requirement with weak or no real evidence, even if the
   deterministic gate scored it "covered" on a keyword hit alone. A keyword match is not
   the same as real depth; say so if the evidence is thin. A hard-requirement gap with
   ZERO evidence anywhere in the profile is a blocking gap — name it plainly, do not
   soften it into a "growth area."
2. **Soft gaps** — nice-to-haves or culture signals with no evidence. Lower stakes,
   still worth naming.
3. **Disqualifiers** — carry forward any the gate already flagged, for the record.
4. **Matches** — what the profile already covers well, citing the specific evidence
   (project or bullet) that proves it. Be specific, not generic ("Python" is not a
   match statement; "hand-rolled hybrid retriever validated against a LangChain
   rebuild" is).
5. **Positioning mismatch** — does the CV's likely framing (its header, its default
   section order) match what THIS JD rewards? E.g. an "AI/ML Engineer" header against a
   JD that is really asking for a data analyst. `null` if there is no mismatch.

## Rules

- A hard-requirement gap with zero evidence CANNOT be rewritten away. Say so; do not
  soften it into a phrasing problem the rewriter can fix with a better sentence.
- Cite evidence by its `source_id` (e.g. `proj.rag_pipeline.b1`), not by re-describing it
  from memory — the rewriter needs the exact id to cite it too.
- Do not invent evidence that was not retrieved. If nothing in the profile plausibly
  answers a requirement, that is a gap, not a stretch.
- Be honest about weak evidence: a 0.56-similarity match that only tangentially relates
  is not a match — say the requirement is under-evidenced.

## Output

Call `emit_diagnosis` exactly once.
```

- [ ] **Step 2: Write the node**

```python
# src/agentic_ai/nodes/diagnose.py
"""diagnose — LLM call 2, Sonnet (plan.md §3, §7.1). Sees the full profile — unlike
extract_requirements, this node's entire job is comparing JD demands against a real CV.
"""

from __future__ import annotations

import functools
import json

from langchain_anthropic import ChatAnthropic

from ..config import settings
from ..profile import Profile
from ..state import Diagnosis, JobState


@functools.lru_cache(maxsize=1)
def _prompt() -> str:
    return (settings.prompts_dir / "diagnose.md").read_text()


@functools.lru_cache(maxsize=1)
def _model():
    llm = ChatAnthropic(model=settings.diagnose_model, temperature=0, max_tokens=4096)
    return llm.with_structured_output(Diagnosis, include_raw=True)


def _requirements_brief(state: JobState) -> str:
    rows = []
    for r in state.requirements:
        ev = ", ".join(f"{e.source_id} (sim={e.similarity:.3f})" for e in r.evidence[:3]) or "none retrieved"
        rows.append(f"- [{r.type}] {r.text}\n  gate coverage: {'covered' if r.covered else 'uncovered'} via {r.covered_by or '—'}\n  top evidence: {ev}")
    return "\n".join(rows)


def _profile_brief(profile: Profile) -> str:
    return json.dumps(
        {
            "skills": sorted(profile.all_tech()),
            "bullets": [
                {"id": b.id, "outcome": b.outcome, "metric": b.metric, "method": b.method, "status": b.status}
                for b in profile.bullets
            ],
        },
        indent=2,
    )


def diagnose(state: JobState, verbose: bool = False) -> dict:
    profile = Profile.load()
    human = (
        f"<job_description>\n{state.job.jd_text.strip()}\n</job_description>\n\n"
        f"<extracted_requirements>\n{_requirements_brief(state)}\n</extracted_requirements>\n\n"
        f"<candidate_profile>\n{_profile_brief(profile)}\n</candidate_profile>"
    )
    messages = [("system", _prompt()), ("human", human)]

    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            result = _model().invoke(messages)
            if verbose:
                print(f"--- diagnose raw response (attempt {attempt}) ---")
                print(result["raw"].content)
            if result["parsing_error"]:
                raise ValueError(result["parsing_error"])
            diagnosis = result["parsed"]
            return {
                "diagnosis": diagnosis,
                "notes": [
                    f"diagnose: {len(diagnosis.hard_gaps)} hard gaps, "
                    f"{len(diagnosis.matches)} matches, "
                    f"positioning={'mismatch' if diagnosis.positioning_mismatch else 'ok'}"
                ],
            }
        except Exception as exc:  # noqa: BLE001 — retried once, then surfaced
            last_error = exc

    raise RuntimeError(f"diagnose failed twice: {last_error}")
```

- [ ] **Step 3: Add the node to the graph (wiring deferred to Task 7)**

In `src/agentic_ai/graph.py`, add the import and register the node (edges added later):

```python
from .nodes.diagnose import diagnose
...
    g.add_node("diagnose", diagnose)
```

- [ ] **Step 4: Smoke-test against a real golden JD (manual, not pytest — matches this
  codebase's precedent of validating LLM nodes against real text, not mocks)**

Run:
```bash
.venv/bin/python -c "
from agentic_ai.graph import job_id
from agentic_ai.state import Job, JobState
from agentic_ai.nodes.requirements import extract_requirements
from agentic_ai.coverage import retrieve_evidence, score_coverage
from agentic_ai.nodes.diagnose import diagnose

jd = open('evals/golden/real_temedica_ws_agentic.txt').read()
job = Job(id=job_id(jd), source='manual', title='', company='', jd_text=jd)
state = JobState(job=job)
state = state.model_copy(update=extract_requirements(state))
state = state.model_copy(update=retrieve_evidence(state))
state = state.model_copy(update=score_coverage(state))
out = diagnose(state, verbose=True)
print(out['diagnosis'].model_dump_json(indent=2))
"
```
Expected: valid JSON, `hard_gaps` names any real gap honestly (Temedica is a known
PROCEED — expect few or no hard gaps), `matches` cites real `source_id`s that appear in
`state.requirements[*].evidence`.

- [ ] **Step 5: Commit**

```bash
git add prompts/diagnose.md src/agentic_ai/nodes/diagnose.py src/agentic_ai/graph.py
git commit -m "Phase 2: diagnose node

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015CT5mSBmUWfoR4aQQDNQiL"
```

---

### Task 5: `prompts/rewrite.md` + `nodes/rewrite.py`

**Files:**
- Create: `prompts/rewrite.md`
- Create: `src/agentic_ai/nodes/rewrite.py`
- Modify: `src/agentic_ai/graph.py` (add node)

**Interfaces:**
- Consumes: `JobState.diagnosis`, `JobState.job`, `Profile.load()`,
  `section_order.classify_role`/`section_order_for`, `JobState.validation_errors` (empty
  on the first call; non-empty on a fact-validator retry — see Task 6)
- Produces: `rewrite(state: JobState) -> dict` (returns `{"draft": Draft,
  "attempt_count": state.attempt_count + 1, "notes": [...]}`)

- [ ] **Step 1: Write the prompt**

```markdown
# prompts/rewrite.md — v1

You rewrite a candidate's CV bullets and cover letter for ONE specific job, using the
diagnosis already done and the candidate's real profile as your only source of facts.

## The X-Y-Z formula (mandatory, every bullet)

"Accomplished [X] as measured by [Y] by doing [Z]" — Google's format.
- X = concrete outcome, not a task description
- Y = a number from the cited profile bullet's `metric` field — if that field is null,
  do NOT invent one; write the bullet without a Y clause. Never fabricate a number.
- Z = the specific tool/method from the cited bullet's `method` field
- Start with a strong action verb (Reduced, Built, Deployed, Automated, Improved,
  Trained, Architected). No passive voice, no "responsible for", no "worked on".

## Every bullet MUST cite a real source

Set `source_bullet_id` to the exact id (e.g. `proj.rag_pipeline.b1`) of the profile
bullet this CV line is built from. You may recombine outcome/metric/method from ONE
cited bullet into a better sentence for this JD — you may NOT combine facts from two
different bullets into one, and you may NOT state a number that is not that bullet's own
`metric` field. If a bullet's `status` is `not_shipped`, the rewritten line must not
imply it is delivered or in production — phrase it as designed/built/prototyped, matching
the honest status.

## What to do

1. Reorder and select bullets per the given `section_order` — do not invent an order.
2. For each section, pick the bullets from the profile that best answer this JD's hard
   requirements first, matched requirements second. Do not use every bullet in the
   profile — 3-5 strong bullets per section beats 8 generic ones.
3. Surface any project from `diagnosis.matches` that is not already prominent.
4. Write a profile line (2-3 sentences) that leads with what THIS JD rewards.
5. Write a cover letter: reference the company and role by name, the 2-3 most relevant
   projects/experience, why Germany / this company, and current availability
   (working-student now, full-time after MSc). If `diagnosis.hard_gaps` is non-empty,
   name the gap directly in one sentence and state why the candidate is still a fit —
   do not dodge it, do not pretend it isn't there.
6. Never claim anything on the profile's `never_claim` list, in any form — not the exact
   sentence, not a paraphrase, not an implication. German fluency above A1/A2 is the one
   you will be most tempted to imply; do not.

## If you are given prior validation errors

You are being asked to fix a specific problem, not start over. Each error names an exact
bullet and an exact false claim (an unverified number, an uncited source, an unevidenced
technology). Fix ONLY what the errors name; keep everything else from the prior attempt
that was not flagged.

## Output

Call `emit_draft` exactly once.
```

- [ ] **Step 2: Write the node**

```python
# src/agentic_ai/nodes/rewrite.py
"""rewrite — LLM call 3, Sonnet (plan.md §3, §7.2). Full profile access; XYZ enforced by
the DraftBullet schema + the fact validator downstream, not by trusting the prompt alone.
"""

from __future__ import annotations

import functools
import json

from langchain_anthropic import ChatAnthropic

from ..config import settings
from ..profile import Profile
from ..section_order import classify_role, section_order_for
from ..state import Draft, JobState


@functools.lru_cache(maxsize=1)
def _prompt() -> str:
    return (settings.prompts_dir / "rewrite.md").read_text()


@functools.lru_cache(maxsize=1)
def _model():
    llm = ChatAnthropic(model=settings.rewrite_model, temperature=0.3, max_tokens=8192)
    return llm.with_structured_output(Draft, include_raw=True)


def _profile_bullets_json(profile: Profile) -> str:
    return json.dumps(
        [
            {"id": b.id, "parent_id": b.parent_id, "outcome": b.outcome, "metric": b.metric,
             "method": b.method, "status": b.status, "tags": b.tags}
            for b in profile.bullets
        ],
        indent=2,
    )


def rewrite(state: JobState, verbose: bool = False) -> dict:
    assert state.diagnosis is not None, "rewrite requires diagnose to have run first"
    profile = Profile.load()
    role = classify_role(state.job.jd_text)
    order = section_order_for(role)

    human_parts = [
        f"<job_description>\n{state.job.jd_text.strip()}\n</job_description>",
        f"<diagnosis>\n{state.diagnosis.model_dump_json(indent=2)}\n</diagnosis>",
        f"<section_order>{order}</section_order>",
        f"<candidate_profile_bullets>\n{_profile_bullets_json(profile)}\n</candidate_profile_bullets>",
        f"<never_claim>\n{profile.never_claim}\n</never_claim>",
    ]
    if state.validation_errors:
        human_parts.append(
            f"<prior_validation_errors>\n{json.dumps(state.validation_errors, indent=2)}\n</prior_validation_errors>"
        )
    messages = [("system", _prompt()), ("human", "\n\n".join(human_parts))]

    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            result = _model().invoke(messages)
            if verbose:
                print(f"--- rewrite raw response (attempt {attempt}) ---")
                print(result["raw"].content)
            if result["parsing_error"]:
                raise ValueError(result["parsing_error"])
            draft = result["parsed"]
            return {
                "draft": draft,
                "attempt_count": state.attempt_count + 1,
                "notes": [f"rewrite attempt {state.attempt_count + 1}: {sum(len(v) for v in draft.bullets.values())} bullets"],
            }
        except Exception as exc:  # noqa: BLE001 — retried once, then surfaced
            last_error = exc

    raise RuntimeError(f"rewrite failed twice: {last_error}")
```

- [ ] **Step 3: Register the node in the graph**

```python
from .nodes.rewrite import rewrite
...
    g.add_node("rewrite", rewrite)
```

- [ ] **Step 4: Smoke-test against the same golden JD, chained after diagnose**

Run the Task 4 script again with these lines appended:

```python
from agentic_ai.nodes.rewrite import rewrite
out2 = rewrite(state.model_copy(update=out), verbose=True)
print(out2['draft'].model_dump_json(indent=2))
"
```
Expected: valid `Draft` JSON; every `source_bullet_id` is a real id from
`evals/golden` context (spot-check a couple against `data/master_profile.yaml`); no
bullet states a number absent from its cited bullet's `metric`.

- [ ] **Step 5: Commit**

```bash
git add prompts/rewrite.md src/agentic_ai/nodes/rewrite.py src/agentic_ai/graph.py
git commit -m "Phase 2: rewrite node

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015CT5mSBmUWfoR4aQQDNQiL"
```

---

### Task 6: `nodes/validate_facts.py` — graph-node wrapper + retry routing

**Files:**
- Create: `src/agentic_ai/nodes/validate_facts.py`
- Modify: `src/agentic_ai/graph.py`
- Test: covered by Task 7's `test_graph_phase2.py` (routing needs the full graph wired)

**Interfaces:**
- Consumes: `state.draft`, `state.requirements` (for `jd_keywords`), `Profile.load()`,
  `validators.facts.validate_facts`
- Produces: `validate_facts_node(state: JobState) -> dict` (returns
  `{"validation_errors": [...], "notes": [...]}`), `fact_gate(state: JobState) ->
  Literal["retry", "clean", "give_up"]` (conditional-edge function)

- [ ] **Step 1: Write the node + gate function**

```python
# src/agentic_ai/nodes/validate_facts.py
"""validate_facts graph node — thin wrapper over validators.facts (plan.md §3, §8).

The routing decision (retry vs. give up) lives here, not in the pure validator, because
it needs `state.attempt_count` — a graph concern, not a fact-checking concern.
"""

from __future__ import annotations

from typing import Literal

from ..config import settings
from ..profile import Profile
from ..state import JobState
from ..validators.facts import validate_facts


def validate_facts_node(state: JobState) -> dict:
    assert state.draft is not None, "validate_facts requires rewrite to have run first"
    profile = Profile.load()
    jd_keywords = [kw for r in state.requirements for kw in r.keywords]
    errors = validate_facts(state.draft, profile, jd_keywords=jd_keywords)
    return {
        "validation_errors": errors,
        "notes": [f"validate_facts: {len(errors)} error(s)" if errors else "validate_facts: clean"],
    }


def fact_gate(state: JobState) -> Literal["retry", "clean", "give_up"]:
    if not state.validation_errors:
        return "clean"
    if state.attempt_count < settings.max_rewrite_attempts:
        return "retry"
    return "give_up"


def log_fact_failure(state: JobState) -> dict:
    """A draft that never passed fact-checking must not reach render_documents."""
    reason = (
        f"fact validation failed after {state.attempt_count} rewrite attempt(s): "
        + "; ".join(state.validation_errors)
    )
    return {"skip_reason": reason, "notes": [f"SKIP — {reason}"]}
```

- [ ] **Step 2: Register nodes in the graph**

```python
from .nodes.validate_facts import fact_gate, log_fact_failure, validate_facts_node
...
    g.add_node("validate_facts", validate_facts_node)
    g.add_node("log_fact_failure", log_fact_failure)
```

- [ ] **Step 3: Commit**

```bash
git add src/agentic_ai/nodes/validate_facts.py src/agentic_ai/graph.py
git commit -m "Phase 2: validate_facts graph node + retry/give-up routing

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015CT5mSBmUWfoR4aQQDNQiL"
```

---

### Task 7: `prompts/review.md` + `nodes/review.py` + full graph wiring

**Files:**
- Create: `prompts/review.md`
- Create: `src/agentic_ai/nodes/review.py`
- Modify: `src/agentic_ai/state.py` (add `Scores.review_score: int | None = None`)
- Modify: `src/agentic_ai/graph.py` (wire every edge from `diagnose` through `review`)
- Test: `tests/test_graph_phase2.py`

**Interfaces:**
- Consumes: `state.draft`, `state.job.jd_text`
- Produces: `review(state: JobState) -> dict` (returns `{"scores": Scores(...,
  review_score=N), "notes": [f"review weaknesses: ..."]}` — weaknesses are logged in
  `notes`, not stored as a new field, since nothing downstream reads them structurally),
  `review_gate(state: JobState) -> Literal["retry", "proceed"]`

- [ ] **Step 1: Add `review_score` to `Scores`**

In `src/agentic_ai/state.py`, `Scores`:

```python
class Scores(BaseModel):
    hard_coverage: float = 0.0
    soft_coverage: float = 0.0
    semantic_fit: float = 0.0
    review_score: int | None = None  # 1..10, filled by the review node
```

- [ ] **Step 2: Write the prompt**

```markdown
# prompts/review.md — v1

Score this CV/cover-letter draft against the job description, 1-10. You are the last
check before this document is scored and possibly sent to an employer — be a demanding
reader, not an agreeable one.

## Score against

- **ATS keyword coverage** — does the draft use the JD's own terms where the profile has
  real evidence for them?
- **Quantification** — does every bullet that can honestly carry a number, carry one?
- **Tone match** — does the draft's register match the JD's (a startup's casual energy
  vs. a Bundesbank posting's formal register)?
- **No fabrication smell** — a claim that reads too good to be true, a number that seems
  suspiciously round or suspiciously precise, a skill claimed nowhere else in the
  profile's usual vocabulary. You cannot verify facts here (that already happened
  deterministically) — flag anything that merely READS as inflated.

## Output

`score`: integer 1-10. Below 7 means "send back to rewrite" downstream — do not be
generous to avoid a retry; a weak draft going to an employer costs more than one more
rewrite pass.
`weaknesses`: specific, actionable. Not "could be stronger" — name the exact bullet or
section and what is wrong with it.

Call `emit_review` exactly once.
```

- [ ] **Step 3: Write the node**

```python
# src/agentic_ai/nodes/review.py
"""review — LLM call 4, Sonnet (plan.md §3, §7.4). Loops to rewrite, max 2 attempts total."""

from __future__ import annotations

import functools

from langchain_anthropic import ChatAnthropic
from pydantic import BaseModel, Field

from ..config import settings
from ..state import JobState, Scores


class ReviewResult(BaseModel):
    """emit_review — the judge's verdict on one draft."""

    score: int = Field(ge=1, le=10, description="1-10. Below 7 sends the draft back to rewrite.")
    weaknesses: list[str] = Field(default_factory=list, description="Specific, actionable — name the bullet.")


@functools.lru_cache(maxsize=1)
def _prompt() -> str:
    return (settings.prompts_dir / "review.md").read_text()


@functools.lru_cache(maxsize=1)
def _model():
    llm = ChatAnthropic(model=settings.review_model, temperature=0, max_tokens=2048)
    return llm.with_structured_output(ReviewResult, include_raw=True)


def review(state: JobState, verbose: bool = False) -> dict:
    assert state.draft is not None, "review requires rewrite to have run first"
    human = (
        f"<job_description>\n{state.job.jd_text.strip()}\n</job_description>\n\n"
        f"<draft>\n{state.draft.model_dump_json(indent=2)}\n</draft>"
    )
    messages = [("system", _prompt()), ("human", human)]

    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            result = _model().invoke(messages)
            if verbose:
                print(f"--- review raw response (attempt {attempt}) ---")
                print(result["raw"].content)
            if result["parsing_error"]:
                raise ValueError(result["parsing_error"])
            verdict = result["parsed"]
            scores = state.scores.model_copy(update={"review_score": verdict.score})
            note = f"review: {verdict.score}/10"
            if verdict.weaknesses:
                note += " — " + "; ".join(verdict.weaknesses)
            return {"scores": scores, "notes": [note]}
        except Exception as exc:  # noqa: BLE001 — retried once, then surfaced
            last_error = exc

    raise RuntimeError(f"review failed twice: {last_error}")


def review_gate(state: JobState) -> str:
    """'retry' back to rewrite (attempts remain and score is low), else 'proceed'."""
    score = state.scores.review_score
    if score is not None and score < settings.min_review_score and state.attempt_count < settings.max_rewrite_attempts:
        return "retry"
    return "proceed"
```

- [ ] **Step 4: Wire the full Phase 2 graph**

Replace `build_graph()` in `src/agentic_ai/graph.py`:

```python
"""tailor_graph — per-job state machine (plan.md §1).

    load_job -> extract_requirements -> retrieve_evidence -> score_coverage
                                                                 |
                                       hard_gap_gate --skip--> log_skip -> END
                                                 \\--proceed
                                                        v
                                                    diagnose -> rewrite -> validate_facts
                                                         ^                       |
                                                         |          errors, attempts left
                                                         +-----------------------+
                                                         |
                                            errors, attempts exhausted -> log_fact_failure -> END
                                                         |
                                                       clean
                                                         v
                                                      review
                                                         |
                                     score<min, attempts left -> back to rewrite (loop above)
                                                         |
                                                     proceed
                                                         v
                                              render_documents -> score_ats -> END

Phase 3 adds recruiter_sim, hiring_manager, the SqliteSaver checkpointer and the human
interrupt (§9). There is nothing to resume yet — one job, one invocation.
"""

from __future__ import annotations

import hashlib

from langgraph.graph import END, StateGraph

from .coverage import hard_gap_gate, log_skip, retrieve_evidence, score_coverage
from .nodes.diagnose import diagnose
from .nodes.requirements import extract_requirements
from .nodes.review import review, review_gate
from .nodes.rewrite import rewrite
from .nodes.validate_facts import fact_gate, log_fact_failure, validate_facts_node
from .render import render_documents
from .scoring.ats import score_ats
from .state import Job, JobState


def job_id(jd_text: str) -> str:
    return hashlib.sha256(jd_text.strip().encode()).hexdigest()[:16]


def load_job(state: JobState) -> dict:
    j = state.job
    return {"notes": [f"loaded job {j.id} — {j.title or '(untitled)'} @ {j.company or '(unknown)'}"]}


def build_graph():
    g = StateGraph(JobState)
    g.add_node("load_job", load_job)
    g.add_node("extract_requirements", extract_requirements)
    g.add_node("retrieve_evidence", retrieve_evidence)
    g.add_node("score_coverage", score_coverage)
    g.add_node("log_skip", log_skip)
    g.add_node("diagnose", diagnose)
    g.add_node("rewrite", rewrite)
    g.add_node("validate_facts", validate_facts_node)
    g.add_node("log_fact_failure", log_fact_failure)
    g.add_node("review", review)
    g.add_node("render_documents", render_documents)
    g.add_node("score_ats", score_ats)

    g.set_entry_point("load_job")
    g.add_edge("load_job", "extract_requirements")
    g.add_edge("extract_requirements", "retrieve_evidence")
    g.add_edge("retrieve_evidence", "score_coverage")
    g.add_conditional_edges(
        "score_coverage", hard_gap_gate, {"skip": "log_skip", "proceed": "diagnose"}
    )
    g.add_edge("log_skip", END)
    g.add_edge("diagnose", "rewrite")
    g.add_edge("rewrite", "validate_facts")
    g.add_conditional_edges(
        "validate_facts",
        fact_gate,
        {"retry": "rewrite", "clean": "review", "give_up": "log_fact_failure"},
    )
    g.add_edge("log_fact_failure", END)
    g.add_conditional_edges("review", review_gate, {"retry": "rewrite", "proceed": "render_documents"})
    g.add_edge("render_documents", "score_ats")
    g.add_edge("score_ats", END)
    return g.compile()


def run(jd_text: str, title: str = "", company: str = "", **job_fields) -> JobState:
    """Run one JD through the graph and return the final state."""
    job = Job(
        id=job_id(jd_text),
        source=job_fields.pop("source", "manual"),
        title=title,
        company=company,
        jd_text=jd_text,
        **job_fields,
    )
    return JobState.model_validate(build_graph().invoke(JobState(job=job)))
```

Note: this task's edges reference `render_documents` (Task 8) and `score_ats` (Task 9),
which do not exist until those tasks run. **Do not run this step's full-suite test until
Task 9 is also done** — steps 4-6 below stub those two nodes as no-ops so this task's own
routing logic (the retry loops) is testable in isolation first.

- [ ] **Step 5: Write a routing test with stub terminal nodes**

Since `render_documents`/`score_ats` don't exist yet, this test builds a *reduced* graph
(same node set through `review`, with `review`'s "proceed" edge pointed straight at `END`)
to prove the retry/give-up routing is correct before the rendering tasks land. This is the
test that actually exercises `fact_gate`/`review_gate` end-to-end with a scripted state —
consistent with this codebase's precedent of not mocking LLM output, by not needing to:
every node under test here is pure Python.

```python
# tests/test_graph_phase2.py
"""Routing logic for the diagnose->rewrite->validate_facts->review loop, tested without
any LLM call — every node exercised here (`fact_gate`, `review_gate`,
`log_fact_failure`) is pure Python over a hand-built JobState.
"""

from __future__ import annotations

from agentic_ai.config import settings
from agentic_ai.nodes.review import review_gate
from agentic_ai.nodes.validate_facts import fact_gate, log_fact_failure
from agentic_ai.state import Draft, Job, JobState, Scores


def _state(**overrides) -> JobState:
    job = Job(id="t", source="manual", title="Test", jd_text="...")
    draft = Draft(profile_line="x", section_order=["projects"], bullets={}, cover_letter="")
    base = dict(job=job, draft=draft)
    base.update(overrides)
    return JobState(**base)


def test_fact_gate_clean_goes_to_review() -> None:
    assert fact_gate(_state(validation_errors=[])) == "clean"


def test_fact_gate_retries_when_attempts_remain() -> None:
    s = _state(validation_errors=["bad number"], attempt_count=1)
    assert settings.max_rewrite_attempts == 2
    assert fact_gate(s) == "retry"


def test_fact_gate_gives_up_when_attempts_exhausted() -> None:
    s = _state(validation_errors=["bad number"], attempt_count=2)
    assert fact_gate(s) == "give_up"


def test_log_fact_failure_names_the_error_and_sets_skip_reason() -> None:
    s = _state(validation_errors=["projects[0]: unverified number '87%'"], attempt_count=2)
    out = log_fact_failure(s)
    assert "87%" in out["skip_reason"]
    assert out["skip_reason"] is not None  # never reaches render_documents with this set


def test_review_gate_retries_on_low_score_with_attempts_left() -> None:
    s = _state(scores=Scores(review_score=5), attempt_count=1)
    assert review_gate(s) == "retry"


def test_review_gate_proceeds_on_low_score_when_attempts_exhausted() -> None:
    """Ship the best version and flag the weakness — never loop forever (CLAUDE.md)."""
    s = _state(scores=Scores(review_score=5), attempt_count=2)
    assert review_gate(s) == "proceed"


def test_review_gate_proceeds_on_passing_score() -> None:
    s = _state(scores=Scores(review_score=8), attempt_count=1)
    assert review_gate(s) == "proceed"
```

- [ ] **Step 6: Run this task's tests (they do not need Task 8/9 to exist)**

Run: `.venv/bin/python -m pytest tests/test_graph_phase2.py -v`
Expected: PASS (7 tests)

- [ ] **Step 7: Commit (graph.py's import of render/score_ats will break `graph.run` until
  Tasks 8-9 land — that is expected and resolved by the end of Task 9; do not run
  `jobpilot add` between this commit and Task 9's)**

```bash
git add prompts/review.md src/agentic_ai/nodes/review.py src/agentic_ai/state.py \
        src/agentic_ai/graph.py tests/test_graph_phase2.py
git commit -m "Phase 2: review node + full graph wiring (diagnose..review loop)

graph.py now imports render_documents/score_ats, landing in the next two
tasks — jobpilot add is broken between this commit and that one.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015CT5mSBmUWfoR4aQQDNQiL"
```

---

### Task 8: Typst rendering — `render.py` + `cover_letter.typ`

**Files:**
- Create: `src/agentic_ai/render.py`
- Create: `templates/cover_letter.typ`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `state.draft`, `Profile.load()`, `settings.chroma_path.parent` (i.e.
  `data/`, for the existing `templates/cv_two_column.typ`'s relative `json("../data/...")`
  load — Task 8 writes a per-job JSON file into `data/` under a job-specific name so the
  existing template's relative path keeps working unmodified)
- Produces: `build_cv_render_data(draft: Draft, profile: Profile) -> dict` (matches
  `data/cv_data.json`'s shape exactly), `build_cover_letter_render_data(draft: Draft,
  profile: Profile, job: Job) -> dict`, `render_documents(state: JobState) -> dict`
  (graph node — writes `out/<job_id>/cv.pdf` and `out/<job_id>/cover_letter.pdf`, returns
  `{"artifacts": {"cv_pdf": "...", "cover_pdf": "..."}, "notes": [...]}`)

- [ ] **Step 1: Write the failing test for the pure data-building functions**

(The Typst compile itself is exercised in Step 4 against the real template — that part
needs the `typst` package installed, already a dependency, no mocking needed.)

```python
# tests/test_render.py
from __future__ import annotations

from agentic_ai.profile import Profile
from agentic_ai.render import build_cover_letter_render_data, build_cv_render_data
from agentic_ai.state import Draft, DraftBullet, Job


def _draft() -> Draft:
    return Draft(
        profile_line="AI/ML Engineer targeting working-student roles.",
        section_order=["projects", "experience", "skills"],
        bullets={
            "projects": [
                DraftBullet(
                    text="Improved recall by 0.833 by building a hybrid BM25+dense retriever.",
                    metric="0.833",
                    source_bullet_id="proj.rag_pipeline.b1",
                )
            ],
            "experience": [
                DraftBullet(
                    text="Resolved 100+ production defects across two carrier platforms.",
                    metric="100+",
                    source_bullet_id="exp.valuemomentum.b1",
                )
            ],
        },
        cover_letter="Dear Hiring Team,\n\nI am excited to apply.\n\nBest,\nDeepak",
        highlighted_projects=["proj.rag_pipeline"],
    )


def test_cv_render_data_matches_existing_template_shape() -> None:
    profile = Profile.load()
    data = build_cv_render_data(_draft(), profile)
    for key in ("name", "tagline", "photo", "contact", "skills", "profile", "projects", "experience"):
        assert key in data
    assert data["projects"][0]["bullets"] == [
        "Improved recall by 0.833 by building a hybrid BM25+dense retriever."
    ]
    assert data["experience"][0]["bullets"] == [
        "Resolved 100+ production defects across two carrier platforms."
    ]


def test_cv_render_data_only_includes_sections_in_section_order() -> None:
    """A section absent from section_order (e.g. no 'skills' bullets drafted) must not
    silently pull in every profile skill — the rewriter's selection is authoritative."""
    profile = Profile.load()
    draft = _draft()
    data = build_cv_render_data(draft, profile)
    assert "skills" in data  # skills come from the profile directly, not from bullets
    assert len(data["projects"]) == 1
    assert len(data["experience"]) == 1


def test_cover_letter_render_data_has_company_and_body() -> None:
    profile = Profile.load()
    job = Job(id="t", source="manual", title="AI Engineer", company="Acme", jd_text="...")
    data = build_cover_letter_render_data(_draft(), profile, job)
    assert data["company"] == "Acme"
    assert data["title"] == "AI Engineer"
    assert "excited to apply" in data["body"]
    assert data["name"] == profile.raw["identity"]["name"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_render.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentic_ai.render'`

- [ ] **Step 3: Write `render.py`**

```python
# src/agentic_ai/render.py
"""render_documents — Typst PDF rendering (plan.md §3, §10).

Two data-building functions produce plain dicts matching the existing hand-written
templates' shapes (`templates/cv_two_column.typ` already consumes exactly the CV shape
below — it was hand-built from `data/cv_data.json`, which this function's output
replaces on a per-job basis). Rendering itself calls the `typst` PyPI package's
`compile()` — no shell binary, no subprocess.
"""

from __future__ import annotations

import json

from typst import compile as typst_compile

from .config import settings
from .profile import Profile
from .state import Draft, Job, JobState

OUT_DIR = settings.chroma_path.parent.parent / "out"


def build_cv_render_data(draft: Draft, profile: Profile) -> dict:
    identity = profile.raw["identity"]
    raw = profile.raw

    def entry_for(bullet_id: str) -> dict | None:
        b = profile.by_id(bullet_id)
        if b is None:
            return None
        for section in ("experience", "projects"):
            for e in raw.get(section, []):
                if e["id"] == b.parent_id:
                    return e
        return None

    def section_block(section_id: str) -> list[dict]:
        blocks: dict[str, dict] = {}
        for db in draft.bullets.get(section_id, []):
            entry = entry_for(db.source_bullet_id)
            if entry is None:
                continue
            key = entry["id"]
            if key not in blocks:
                if section_id == "experience":
                    org = entry["company"]
                    if entry.get("clients"):
                        org += " · clients: " + ", ".join(c.split(" (")[0] for c in entry["clients"])
                    blocks[key] = {
                        "title": entry["title"], "org": org,
                        "dates": f"{entry.get('start', '')} – {entry.get('end', 'present')}",
                        "bullets": [],
                    }
                else:
                    meta = " · ".join(entry.get("stack", []))
                    blocks[key] = {"title": entry["title"], "meta": meta, "bullets": []}
            blocks[key]["bullets"].append(db.text)
        return list(blocks.values())

    return {
        "name": identity["name"].upper(),
        "tagline": identity.get("tagline_keywords", []),
        "photo": "assets/photo.jpg",
        "contact": [
            ["mail", identity["email"]], ["phone", identity["phone"]],
            ["pin", identity["location"]], ["link", identity["github"]],
            ["link", identity["linkedin"]],
        ],
        "skills": [[cat, ", ".join(s["name"] for s in items)] for cat, items in raw.get("skills", {}).items()],
        "education": raw.get("education", []),
        "certifications": raw.get("certifications", []),
        "languages": raw.get("constraints", {}).get("languages", []),
        "profile": draft.profile_line,
        "projects": section_block("projects"),
        "experience": section_block("experience"),
    }


def build_cover_letter_render_data(draft: Draft, profile: Profile, job: Job) -> dict:
    identity = profile.raw["identity"]
    return {
        "name": identity["name"],
        "email": identity["email"],
        "phone": identity["phone"],
        "location": identity["location"],
        "company": job.company or "Hiring Team",
        "title": job.title or "the role",
        "body": draft.cover_letter,
    }


def render_documents(state: JobState) -> dict:
    assert state.draft is not None, "render_documents requires a clean draft"
    profile = Profile.load()
    job_dir = OUT_DIR / state.job.id
    job_dir.mkdir(parents=True, exist_ok=True)

    cv_data = build_cv_render_data(state.draft, profile)
    cv_json_path = settings.profile_path.parent / f"_render_{state.job.id}_cv.json"
    cv_json_path.write_text(json.dumps(cv_data, indent=2))
    cv_pdf = job_dir / "cv.pdf"
    typst_compile(
        str(settings.prompts_dir.parent / "templates" / "cv_two_column.typ"),
        output=str(cv_pdf),
        sys_inputs={"data": str(cv_json_path)},
        root=str(settings.prompts_dir.parent),
    )
    cv_json_path.unlink()

    letter_data = build_cover_letter_render_data(state.draft, profile, state.job)
    letter_json_path = settings.profile_path.parent / f"_render_{state.job.id}_letter.json"
    letter_json_path.write_text(json.dumps(letter_data, indent=2))
    letter_pdf = job_dir / "cover_letter.pdf"
    typst_compile(
        str(settings.prompts_dir.parent / "templates" / "cover_letter.typ"),
        output=str(letter_pdf),
        sys_inputs={"data": str(letter_json_path)},
        root=str(settings.prompts_dir.parent),
    )
    letter_json_path.unlink()

    return {
        "artifacts": {"cv_pdf": str(cv_pdf), "cover_pdf": str(letter_pdf)},
        "notes": [f"rendered {cv_pdf.name} and {letter_pdf.name} to {job_dir}"],
    }
```

`templates/cv_two_column.typ` currently hardcodes `json("../data/cv_data.json")` (see
Step 3b below — it must switch to `json(sys.inputs.data)` for the per-job path above to
work; this is the one existing-file edit this task makes, and it is backward compatible
because `jobpilot` never called it any other way before Phase 2).

- [ ] **Step 3b: Point the existing CV template at the per-job data file**

In `templates/cv_two_column.typ`, change line 4 from:
```typst
#let d = json("../data/cv_data.json")
```
to:
```typst
#let d = json(sys.inputs.data)
```

- [ ] **Step 4: Write the cover letter template**

```typst
// templates/cover_letter.typ — data-driven, renders from a per-job JSON file
#let d = json(sys.inputs.data)

#set page(paper: "a4", margin: (x: 2.2cm, y: 2.4cm))
#set text(font: "Carlito", size: 10.5pt, lang: "en")
#set par(justify: true, leading: 0.65em)

#d.name \
#d.email · #d.phone · #d.location

#v(1.2em)
#datetime.today().display("[month repr:long] [day], [year]")

#v(1.2em)
Re: Application for #d.title at #d.company

#v(1em)
#d.body.split("\n\n").join([#v(0.8em)])
```

- [ ] **Step 5: Run the data-building tests**

Run: `.venv/bin/python -m pytest tests/test_render.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Smoke-test an actual Typst compile (proves the template edit + the new
  cover letter template both compile, not just that the dicts look right)**

Run:
```bash
.venv/bin/python -c "
import json
from pathlib import Path
from agentic_ai.profile import Profile
from agentic_ai.render import build_cv_render_data, build_cover_letter_render_data
from agentic_ai.state import Draft, DraftBullet, Job
from typst import compile as typst_compile

profile = Profile.load()
draft = Draft(
    profile_line='AI/ML Engineer.',
    section_order=['projects'],
    bullets={'projects': [DraftBullet(text='Improved recall by 0.833.', metric='0.833', source_bullet_id='proj.rag_pipeline.b1')]},
    cover_letter='Dear team,\n\nExcited to apply.\n\nBest,\nDeepak',
)
Path('/tmp/cv.json').write_text(json.dumps(build_cv_render_data(draft, profile)))
typst_compile('templates/cv_two_column.typ', output='/tmp/cv_smoke.pdf', sys_inputs={'data': '/tmp/cv.json'}, root='.')
print('cv.pdf ok:', Path('/tmp/cv_smoke.pdf').stat().st_size, 'bytes')

job = Job(id='t', source='manual', title='AI Engineer', company='Acme', jd_text='...')
Path('/tmp/letter.json').write_text(json.dumps(build_cover_letter_render_data(draft, profile, job)))
typst_compile('templates/cover_letter.typ', output='/tmp/letter_smoke.pdf', sys_inputs={'data': '/tmp/letter.json'}, root='.')
print('letter.pdf ok:', Path('/tmp/letter_smoke.pdf').stat().st_size, 'bytes')
"
```
Expected: both PDFs written with nonzero size, no Typst compile error. If the CV template
throws on the `sys.inputs.data` change, check `templates/cv_two_column.typ` for any other
place `d` is loaded (there should be exactly one).

- [ ] **Step 7: Commit**

```bash
git add src/agentic_ai/render.py templates/cover_letter.typ templates/cv_two_column.typ \
        tests/test_render.py
git commit -m "Phase 2: Typst rendering — render_documents node + cover letter template

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015CT5mSBmUWfoR4aQQDNQiL"
```

---

### Task 9: `scoring/ats.py` — deterministic ATS score + PDF parseability

**Files:**
- Create: `src/agentic_ai/scoring/__init__.py` (empty)
- Create: `src/agentic_ai/scoring/ats.py`
- Modify: `pyproject.toml` (add `pypdf`)
- Test: `tests/test_ats_score.py`

**Interfaces:**
- Consumes: `state.draft`, `state.requirements`, `state.validation_errors`,
  `state.artifacts["cv_pdf"]`, `Profile.load()`
- Produces: `AtsScore` (Pydantic: `total: float`, `verdict: Literal["apply",
  "fix_then_apply", "skip"]`, `gates: dict[str, bool]`, `components: dict[str, float]`,
  `report: str`), `ats_score(state: JobState, pdf_path: Path) -> AtsScore`,
  `score_ats(state: JobState) -> dict` (graph node — adds `JobState.ats: AtsScore | None`
  field to `state.py` in Step 1 of this task, returns `{"ats": AtsScore, "notes": [...]}`)

- [ ] **Step 1: Add the `pypdf` dependency and the `AtsScore` state field**

In `pyproject.toml`, add to `dependencies`, in the `# storage & output` block:
```toml
    "pypdf>=5.0",
```

Run: `cd /Users/deepakkatukuri/Agentic_ai && uv add pypdf` (or `.venv/bin/pip install "pypdf>=5.0"`
if `uv` is not driving this environment — check which one manages `.venv` first with
`ls uv.lock` before choosing; `uv.lock` exists in this repo, so use `uv add`).

In `src/agentic_ai/state.py`, add near the top (after the `ReqType`/`EvidenceSource`
literals):
```python
AtsVerdict = Literal["apply", "fix_then_apply", "skip"]


class AtsScore(BaseModel):
    """plan.md §6. Counts first, arithmetic second — a score with no fact table under it
    is invalid, so `report` always carries both."""

    total: float
    verdict: AtsVerdict
    gates: dict[str, bool]  # gate name -> passed
    components: dict[str, float]  # component name -> points earned
    report: str  # the human-readable breakdown, plan.md §6 "Report format"
```
And add `ats: AtsScore | None = None` to `JobState`, after `validation_errors`.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_ats_score.py
"""plan.md §6 — gates are a hard zero, points are counted not asserted."""

from __future__ import annotations

from agentic_ai.profile import Profile
from agentic_ai.scoring.ats import ParsedPdf, ats_score, gates_failed
from agentic_ai.state import Draft, DraftBullet, Job, JobState, Requirement, Scores


def _profile() -> Profile:
    return Profile.load()


def _clean_state(review_score: int = 9) -> JobState:
    job = Job(id="t", source="manual", title="AI Engineer", company="Acme", jd_text="...")
    draft = Draft(
        profile_line="x",
        section_order=["projects"],
        bullets={
            "projects": [
                DraftBullet(text="Improved recall by 0.833.", metric="0.833", source_bullet_id="proj.rag_pipeline.b1")
            ]
        },
        cover_letter="x",
    )
    reqs = [
        Requirement(text="Python", type="hard", keywords=["python"], covered=True, covered_by="keyword"),
        Requirement(text="RAG", type="hard", keywords=["rag"], covered=True, covered_by="semantic"),
    ]
    return JobState(
        job=job, draft=draft, requirements=reqs, validation_errors=[],
        scores=Scores(hard_coverage=1.0, review_score=review_score),
    )


def test_gates_pass_with_no_validation_errors_and_no_disqualifier() -> None:
    assert gates_failed(_clean_state()) == {}


def test_gates_fail_when_validation_errors_present() -> None:
    s = _clean_state().model_copy(update={"validation_errors": ["projects[0]: unverified number '99%'"]})
    failed = gates_failed(s)
    assert failed.get("no_fabrication") is False


def test_gates_fail_on_unmet_disqualifier() -> None:
    s = _clean_state()
    s = s.model_copy(update={"requirements": s.requirements + [
        Requirement(text="C1 German", type="disqualifier", keywords=["german"], covered=False)
    ]})
    failed = gates_failed(s)
    assert failed.get("no_disqualifiers") is False


def test_a_failed_gate_zeroes_the_total_regardless_of_points() -> None:
    profile = _profile()
    s = _clean_state().model_copy(update={"validation_errors": ["bad number"]})
    pdf = ParsedPdf(recovered=10, expected=10)
    result = ats_score(s, pdf, profile)
    assert result.total == 0.0
    assert result.verdict == "skip"


def test_clean_high_coverage_state_scores_high_and_reports_counts() -> None:
    profile = _profile()
    s = _clean_state(review_score=9)
    pdf = ParsedPdf(recovered=14, expected=14)
    result = ats_score(s, pdf, profile)
    assert result.total > 0
    assert "hard req coverage" in result.report
    assert result.gates == {"no_fabrication": True, "no_disqualifiers": True}


def test_verdict_thresholds() -> None:
    from agentic_ai.scoring.ats import verdict_for

    assert verdict_for(96) == "apply"
    assert verdict_for(95) == "apply"
    assert verdict_for(94) == "fix_then_apply"
    assert verdict_for(90) == "fix_then_apply"
    assert verdict_for(89.9) == "skip"


def test_pdf_parseability_recovers_expected_fields_from_a_real_render(tmp_path) -> None:
    """Component 3 depends on re-extracting text from the ACTUAL rendered PDF, not a
    guess — this is the failure mode that silently loses interviews (plan.md §6)."""
    import json

    from agentic_ai.render import build_cv_render_data, render_documents
    from agentic_ai.state import Job

    profile = _profile()
    draft = Draft(
        profile_line="AI/ML Engineer.",
        section_order=["projects"],
        bullets={"projects": [DraftBullet(text="Improved recall by 0.833.", metric="0.833", source_bullet_id="proj.rag_pipeline.b1")]},
        cover_letter="x",
    )
    job = Job(id="pdftest", source="manual", title="AI Engineer", company="Acme", jd_text="...")
    state = JobState(job=job, draft=draft)
    out = render_documents(state)
    from pathlib import Path

    from agentic_ai.scoring.ats import parse_pdf

    parsed = parse_pdf(Path(out["artifacts"]["cv_pdf"]), profile)
    assert parsed.recovered > 0
    assert parsed.expected > 0
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `mkdir -p src/agentic_ai/scoring && touch src/agentic_ai/scoring/__init__.py && .venv/bin/python -m pytest tests/test_ats_score.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentic_ai.scoring.ats'`

- [ ] **Step 4: Write `ats.py`**

```python
# src/agentic_ai/scoring/ats.py
"""ats_score — the deterministic, computed ATS score (plan.md §6, CLAUDE.md ATS Score).

Never asserted from judgement. Gates are a hard zero — fabrication cannot be offset by
scoring well elsewhere. Points are counted, not eyeballed: every component below states
its numerator and denominator, and `report` prints both before the arithmetic.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel
from pypdf import PdfReader

from ..profile import Profile
from ..state import AtsScore, AtsVerdict, JobState


class ParsedPdf(BaseModel):
    recovered: int
    expected: int


def gates_failed(state: JobState) -> dict[str, bool]:
    """Only entries that FAILED — an empty dict means every gate passed."""
    gates = {
        "no_fabrication": not state.validation_errors,
        "no_disqualifiers": not any(r.type == "disqualifier" and not r.covered for r in state.requirements),
    }
    return {name: passed for name, passed in gates.items() if not passed}


def verdict_for(total: float) -> AtsVerdict:
    if total >= 95:
        return "apply"
    if total >= 90:
        return "fix_then_apply"
    return "skip"


def _frac(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0


def parse_pdf(pdf_path: Path, profile: Profile) -> ParsedPdf:
    """Re-extract text from the RENDERED pdf and check what survives — the actual
    failure mode that loses interviews, not a guess about whether Typst rendered ok."""
    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(pdf_path)).pages)
    identity = profile.raw["identity"]
    expected_fields = {
        "name": identity["name"].split()[0],
        "email": identity["email"],
        "phone": identity["phone"].replace(" ", ""),
        "location": identity["location"].split(",")[0].split("(")[0].strip(),
    }
    recovered = sum(1 for v in expected_fields.values() if v and v.replace(" ", "") in text.replace(" ", ""))
    expected = len(expected_fields)
    for cat, items in profile.raw.get("skills", {}).items():
        for s in items:
            expected += 1
            if s["name"].lower() in text.lower():
                recovered += 1
    return ParsedPdf(recovered=recovered, expected=expected)


def ats_score(state: JobState, pdf: ParsedPdf, profile: Profile) -> AtsScore:
    failed = gates_failed(state)
    hard = [r for r in state.requirements if r.type == "hard"]
    hard_covered = sum(r.covered for r in hard)

    evidenced_terms = {kw for r in state.requirements for kw in r.keywords if r.covered}
    verbatim_hits = sum(
        1 for r in state.requirements for kw in r.keywords if r.covered and kw.lower() in evidenced_terms
    ) if evidenced_terms else 0

    all_bullets = [b for bullets in (state.draft.bullets.values() if state.draft else []) for b in bullets]
    metric_available = sum(1 for b in all_bullets if profile.by_id(b.source_bullet_id) and profile.by_id(b.source_bullet_id).metric)
    metric_present = sum(1 for b in all_bullets if b.metric)

    components = {
        "hard req coverage": 50 * _frac(hard_covered, len(hard)) if hard else 50.0,
        "literal keywords": 20 * _frac(len(evidenced_terms), max(len(evidenced_terms), 1)),
        "pdf parseability": 15 * _frac(pdf.recovered, pdf.expected),
        "quantification": 10 * _frac(metric_present, max(metric_available, 1)) if metric_available else 10.0,
        "positioning": 5.0 if state.draft and state.draft.section_order else 0.0,
    }
    total = 0.0 if failed else round(sum(components.values()), 1)
    verdict: AtsVerdict = "skip" if failed else verdict_for(total)

    gate_lines = "\n".join(
        f"  {name.replace('_', ' ')} {'.' * (18 - len(name))} {'0 ✓' if name not in failed else 'FAILED'}"
        for name in ("no_fabrication", "no_disqualifiers")
    )
    point_lines = "\n".join(f"  {name} {'.' * (16 - len(name))} {pts:5.1f}" for name, pts in components.items())
    report = (
        f"ATS SCORE: {total:.1f} / 100          verdict: {verdict}\n\n"
        f"GATES\n{gate_lines}\n\n"
        f"POINTS\n{point_lines}\n                                   ─────\n                                   {total:6.1f}\n"
    )

    return AtsScore(
        total=total, verdict=verdict,
        gates={"no_fabrication": "no_fabrication" not in failed, "no_disqualifiers": "no_disqualifiers" not in failed},
        components=components, report=report,
    )


def score_ats(state: JobState) -> dict:
    assert state.artifacts.get("cv_pdf"), "score_ats requires render_documents to have run first"
    profile = Profile.load()
    pdf = parse_pdf(Path(state.artifacts["cv_pdf"]), profile)
    result = ats_score(state, pdf, profile)
    return {"ats": result, "notes": [f"ats_score: {result.total}/100 -> {result.verdict}"]}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_ats_score.py -v`
Expected: PASS (7 tests)

- [ ] **Step 6: Run the full suite — graph.py's imports now resolve end-to-end**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all pass (Phase 1's 68 + this plan's new tests)

- [ ] **Step 7: Full end-to-end smoke test on a real golden JD**

Run:
```bash
.venv/bin/python -c "
from agentic_ai.graph import run
state = run(open('evals/golden/real_temedica_ws_agentic.txt').read(), title='AI Engineer', company='Temedica')
print('skip_reason:', state.skip_reason)
if state.ats:
    print(state.ats.report)
print('artifacts:', state.artifacts)
"
```
Expected: no exception; if Temedica still PROCEEDs (it did in Phase 1), a non-empty
`ats.report` and two real PDF paths under `out/<job_id>/`.

- [ ] **Step 8: Commit**

```bash
git add src/agentic_ai/scoring src/agentic_ai/state.py pyproject.toml uv.lock \
        tests/test_ats_score.py
git commit -m "Phase 2: deterministic ATS score + PDF parseability check

Closes the Phase 2 build order from plan.md §16: diagnose -> rewrite ->
validate_facts -> review is now fully wired, with Typst rendering and the
computed ATS score at the end of the proceed branch.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015CT5mSBmUWfoR4aQQDNQiL"
```

---

### Task 10: CLI — print the diagnosis, draft, and ATS report; wire `--no-render`

**Files:**
- Modify: `src/agentic_ai/cli.py`

**Interfaces:**
- Consumes: `JobState.diagnosis`, `.draft`, `.ats`, `.artifacts`, `.skip_reason`
  (existing `_report` already handles the skip/gap-report path from Phase 1 — this task
  only adds what prints when the job proceeds all the way through)

- [ ] **Step 1: Add a `--no-render` flag (useful while iterating on prompts — skips the
  Typst/ATS tail so `jobpilot add` only pays for diagnose/rewrite/review)**

In `add()`'s signature, add:
```python
    no_render: bool = typer.Option(False, "--no-render", help="Stop after review; skip PDF render + ATS score."),
```

This requires the graph to support stopping early. Simplest correct approach: don't
branch the graph itself (that would need a second compiled graph) — instead, run the
existing graph, and if `no_render` was requested, note it will still render (documented
limitation) OR — cleaner — build the graph once per call with the render tail included
only when needed:

```python
def add(
    ...
    no_render: bool = typer.Option(False, "--no-render", help="Stop after review; skip PDF render + ATS score."),
) -> None:
    ...
    from .graph import run

    state = run(
        jd_text, title=title, company=company, location=location,
        employment_type="fulltime" if full_time else "werkstudent",
        _skip_render=no_render,
    )
```

And in `graph.py`'s `run()`, thread `_skip_render` through to `build_graph`:

```python
def build_graph(skip_render: bool = False):
    g = StateGraph(JobState)
    ...
    g.add_conditional_edges("review", review_gate, {"retry": "rewrite", "proceed": "render_documents"})
    if skip_render:
        g.add_edge("review", END)  # NOTE: replace the line above with this in that branch
    ...
```

Concretely, replace the single `add_conditional_edges("review", ...)` call with:
```python
    g.add_conditional_edges(
        "review", review_gate,
        {"retry": "rewrite", "proceed": END if skip_render else "render_documents"},
    )
    if not skip_render:
        g.add_edge("render_documents", "score_ats")
        g.add_edge("score_ats", END)
```

And update `run()`:
```python
def run(jd_text: str, title: str = "", company: str = "", **job_fields) -> JobState:
    skip_render = job_fields.pop("_skip_render", False)
    job = Job(
        id=job_id(jd_text),
        source=job_fields.pop("source", "manual"),
        title=title, company=company, jd_text=jd_text, **job_fields,
    )
    return JobState.model_validate(build_graph(skip_render=skip_render).invoke(JobState(job=job)))
```

- [ ] **Step 2: Extend `_report` to print the Phase 2 sections**

Add, in `_report`, right after the existing `if state.skip_reason: ... else: ...` block
(so it only prints when the job proceeded through the gate — a skip never reaches
diagnose):

```python
    if state.diagnosis and not state.skip_reason:
        console.print("\n[bold]DIAGNOSIS[/bold]")
        if state.diagnosis.hard_gaps:
            console.print("[red]hard gaps:[/red] " + "; ".join(state.diagnosis.hard_gaps))
        if state.diagnosis.positioning_mismatch:
            console.print(f"[yellow]positioning:[/yellow] {state.diagnosis.positioning_mismatch}")
        if state.diagnosis.matches:
            console.print("[green]matches:[/green] " + "; ".join(state.diagnosis.matches[:5]))

    if state.draft:
        console.print(f"\n[bold]DRAFT[/bold]  ({state.attempt_count} attempt(s))")
        console.print(f"profile line: {state.draft.profile_line}")
        console.print(f"sections: {' -> '.join(state.draft.section_order)}")
        if state.scores.review_score is not None:
            console.print(f"review score: {state.scores.review_score}/10")

    if state.ats:
        console.print(f"\n{state.ats.report}")

    if state.artifacts:
        console.print("[bold]artifacts:[/bold]")
        for k, v in state.artifacts.items():
            console.print(f"  {k}: {v}")
```

- [ ] **Step 3: Manual verification (no automated test — this is print-formatting over
  already-tested state; matches Phase 1's `_report`, which also has no dedicated test)**

Run:
```bash
.venv/bin/jobpilot add --file evals/golden/real_temedica_ws_agentic.txt --location "Munich, Germany"
```
Expected: gap report (Phase 1, unchanged) followed by DIAGNOSIS, DRAFT, the ATS report
table, and two `artifacts:` lines pointing at real files under `out/`.

- [ ] **Step 4: Run the full test suite one more time**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/agentic_ai/cli.py src/agentic_ai/graph.py
git commit -m "Phase 2: CLI prints diagnosis/draft/ATS report; --no-render flag

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015CT5mSBmUWfoR4aQQDNQiL"
```

- [ ] **Step 6: Update `tasks/todo.md` with a Phase 2 section (mirrors the Phase 1
  closing section's format — shipped summary, bugs found, what's still open)**

This step has no fixed content to paste (it depends on what Step 3's manual run actually
surfaces) — write it the same way Phase 1's closing section was written: what shipped,
which real bugs were hit and fixed while building (there will be some — Typst template
edge cases, a rewrite prompt producing a `source_bullet_id` that doesn't parse, an ATS
component denominator that's zero on a thin draft), and what's carried into Phase 3
(recruiter_sim, hiring_manager, SQLite persistence, the human interrupt). Commit
separately, same pattern as the Phase 1 bug-pass commit earlier in this repo's history.

---

## Self-Review Notes

**Spec coverage** — plan.md §16 Phase 2 bullets, each mapped to a task:
- `diagnose → rewrite → validate_facts → review` loop: Tasks 3-7
- `validate_facts` unit tests first: Task 2, ordered before Task 5 (rewrite)
- Typst templates, PDF output: Task 8
- `ats_score` + PDF parseability check: Task 9

§6's ATS rubric (5 components, the report format, the two "keeps it honest" properties)
is implemented verbatim in Task 9. §7's five roles: `diagnose`/`rewrite`/`review` are
built (Tasks 4/5/7); `recruiter_sim`/`hiring_manager` are explicitly Phase 3 per §16 and
out of scope here. §7's section ordering: Task 3. §8's fact validator: Task 2, including
its own explicit adversarial-test instruction.

**Known scope cuts, stated rather than hidden** (each already called out inline above):
- `validate_facts`'s employer/title/dates gate is satisfied structurally (via
  `source_bullet_id` provenance) rather than by free-text scanning for invented company
  names — a general invented-proper-noun detector is out of reach without its own NLP
  layer and isn't in plan.md §8's own reference implementation either.
- Broader `never_claim` enforcement covers the one cleanly-implementable case (language
  fluency, reusing Phase 1's `coverage.py` logic) — the remaining entries are prose
  claims with no single detection pattern. Task 10 Step 6 is where this gets written down
  as an explicit "Still open" item, matching how Phase 1 handled its own residual gaps.
- No SQLite / cost ledger / tracing in this phase — plan.md §16 puts those in Phase 3/4.

**Type consistency check** — `DraftBullet.source_bullet_id` (Task 1) is the field every
downstream consumer reads by that exact name: `validators/facts.py` (Task 2),
`render.py`'s `entry_for()` (Task 8), `scoring/ats.py`'s quantification component (Task
9). `state.attempt_count` (Task 1) is incremented only in `rewrite()` (Task 5) and read
only in `fact_gate`/`review_gate` (Tasks 6-7) — one writer, two readers, no drift.
`settings.max_rewrite_attempts` (Task 3) is the single constant both gates compare
against.
