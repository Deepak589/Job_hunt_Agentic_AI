# JobPilot

Multi-agent job search and CV tailoring pipeline. Run a job description through a
LangGraph pipeline (requirements extraction → diagnosis → rewrite → fact validation →
review → ATS scoring) and get a gap report, tailored CV/cover-letter PDFs, and a
computed ATS score.

## Pipeline

1. **Requirements** — extract hard/soft/disqualifier requirements from the JD.
2. **Diagnosis** — match requirements against evidence in your profile; flag gaps
   and positioning mismatches.
3. **Rewrite** — draft tailored bullets/profile line, only if no blocking gap.
4. **Fact validation** — reject any claim (skill, number, employer) with no evidence
   in `master_profile.yaml`.
5. **Review** — score the draft against keyword coverage, quantification, tone.
6. **Render + ATS score** — typeset CV/cover letter (Typst → PDF) and compute the
   ATS score from a counted fact table (`src/agentic_ai/scoring/ats.py`).

Skip renders with `add --no-render` to stop after the gap report.

## Setup

```bash
uv sync
cp .env.example .env   # set ANTHROPIC_API_KEY
```

Requires Python 3.13+. Uses `uv` for dependency management.

## Usage

```bash
jobpilot add --file jd.txt --title "Data Scientist" --company Acme --location Berlin
jobpilot add --stdin < jd.txt          # pipe a JD in
jobpilot add --file jd.txt --full-time # lift the werkstudent weekly-hours cap
jobpilot add --file jd.txt --no-render # gap report only, skip PDF + ATS score

jobpilot index build [--force]         # (re)build the evidence store from master_profile.yaml
jobpilot index calibrate               # measure SEM_THRESHOLD against labeled probes

jobpilot profile check                 # validate master_profile.yaml integrity
```

## Layout

```
src/agentic_ai/
  graph.py          LangGraph pipeline wiring
  nodes/             requirements, diagnose, rewrite, validate_facts, review
  scoring/ats.py      ATS score fact-table + arithmetic
  validators/facts.py fabrication gate
  evidence.py         Chroma-backed semantic evidence store
  profile.py          master_profile.yaml loader/validator
  render.py            Typst PDF rendering
data/master_profile.yaml   source of truth: bullets, skills, evidence
templates/*.typ            CV + cover letter templates
tests/                      pytest suite
evals/                      golden + real JD fixtures
```

## Testing

```bash
pytest
```

See `CLAUDE.md` for the full agent workflow spec (five-role pipeline, ATS scoring
rubric, CV bullet style) and `tasks/todo.md` for open items.
