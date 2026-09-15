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
        # By the time score_ats runs, state.validation_errors is always empty — the
        # graph's fact_gate/log_fact_failure edges already sent a fabricated draft to
        # END before render_documents/score_ats ever run. This is defense-in-depth,
        # not the primary enforcement.
        "no_fabrication": not state.validation_errors,
        "no_disqualifiers": not state.unmet_disqualifiers(),
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
