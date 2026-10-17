# JobPilot

Job description in → evidence-backed fit verdict → tailored CV + cover letter → rendered PDFs.
**It never applies.** Every run stops at a human checkpoint; there is no code path that sends,
submits or posts.

The pipeline is deliberately slim: **one reasoning call** wrapped in deterministic guards, with a
human as the reviewer. It mirrors the manual flow in `Examples/CLAUDE.md`.

## Pipeline

```
load_job → agent_cv  (ONE LLM call: requirements + verdicts + full draft)
  ├─ hard gap / unmet disqualifier → log_skip → END
  └─ validate_facts  (deterministic, BLOCKING; retry max 2 via rewrite)
       ├─ fail → log_fact_failure → END
       └─ [human_review: interrupt()] → render_documents → score_ats
```

1. **`agent_cv`** — one Sonnet call reads the JD and `master_profile.yaml` together and returns
   the requirement list (hard / soft / disqualifier), a per-requirement verdict
   (`met` / `unmet` / `unknown`), and the full CV + cover-letter draft.
   Set `JOBPILOT_USE_AGENT_CV=false` to use the two-call path instead (`analyze` → `rewrite`);
   only that one node changes, everything around it is shared.
2. **Rails (code, not model)** — language demands are a CEFR comparison (`languages.py`) and
   on-site clauses are checked against your location (`coverage.py`), whatever the model said.
   A language demand never skips a job — it surfaces at review instead.
3. **Hard-gap gate** — categorical, not a threshold: an unmet disqualifier, or more than
   `max_blocking_hard_gaps` (3) unmet hard requirements, skips the job.
4. **`validate_facts`** — deterministic Python, never an LLM. Rejects any skill, number,
   employer or claim with no evidence in the profile. Umbrella terms ("LLMs", "Agentic AI")
   resolve only through scoped aliases (`validators/aliases.py`). A failure retries through
   `rewrite` with the exact errors, never back through `agent_cv`.
5. **Human review** — `interrupt()` on a SQLite checkpointer: approve / edit / reject.
   Edits are re-validated and recorded to `data/preferences.yaml` for future drafts.
6. **Render + ATS score** — Jinja2 HTML → headless Chrome `--print-to-pdf`, then a computed
   ATS score from a counted fact table (`scoring/ats.py`).

## Setup

```bash
uv sync --extra dev
cp .env.example .env              # set ANTHROPIC_API_KEY
jobpilot profile init --cv main_cv_v2.pdf
jobpilot profile check            # drift gate — run before any CV build
jobpilot index build
```

Python 3.13, `uv`. Anthropic is the only LLM provider; embeddings (`BAAI/bge-m3`) and the
reranker run locally. Rendering needs Chrome/Chromium installed.

## Usage

```bash
# Add jobs (manual JD input is the primary path)
jobpilot add --file jd.txt --title "ML Engineer" --company Acme --location Berlin
jobpilot add --url <posting-url>       # JSON-LD JobPosting, falls back to trafilatura
jobpilot add --dir jds/                # every .txt in a folder, concurrency 3
jobpilot add --file jd.txt --review    # pause at the human checkpoint
jobpilot add --file jd.txt --no-render # verdict + draft only, no PDF

# Review and track
jobpilot review <job_id> [-v]          # approve / edit / reject
jobpilot resume <job_id>               # continue a crashed run from its checkpoint
jobpilot applied <job_id>              # record that you sent it (you click submit, not JobPilot)
jobpilot outcome <job_id> interview|reject|ghost

# Cost and ops
jobpilot cost [--days N]               # spend from the runs ledger
jobpilot index build [--force] | index calibrate

# Ingest (supplement to manual input)
jobpilot source arbeitnow --query "Data Scientist" [--run]
jobpilot source adzuna --query "Data Scientist"     # needs ADZUNA_APP_ID / ADZUNA_APP_KEY
jobpilot run [--batch] [--digest json] # poll config/companies.yaml (personio, greenhouse, lever, ashby)

# Comparison harness — report-only, never a gate
jobpilot agent-draft --file jd.txt [--render] [--json]

# Your own CV
./build_cv.sh                          # data/cv_data.json → main_cv_v2.pdf
```

Useful settings (env prefix `JOBPILOT_`, or `.env`):

| Setting | Default | Effect |
|---|---|---|
| `USE_AGENT_CV` | `true` | one-call `agent_cv` vs two-call `analyze` → `rewrite`. Use `false` for skip-heavy batches (a skip costs ~$0.05 instead of ~$0.18). |
| `MAX_DAILY_COST_USD` | unset | refuse new runs once today's spend hits the cap |
| `MAX_LANGUAGE_GAP_STEPS` | `5` | `1` makes a language demand >1 CEFR step above the profile a skip again |

Dedupe is by content hash, not URL — reposted jobs cost nothing.

## Claude Desktop (MCP)

```json
{
  "mcpServers": {
    "jobpilot": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/Agentic_ai", "jobpilot-mcp"]
    }
  }
}
```

Tools: `list_pending_reviews`, `get_job`, `get_applicant_profile`, `add_job`, `review_job`,
`mark_applied`. None of them submit an application.

## Layout

```
src/agentic_ai/
  graph.py             LangGraph wiring (both topologies), run / run_many / run_for_review
  nodes/               agent_cv, analyze, rewrite, validate_facts
  coverage.py          on-site + language rails, hard-gap gate inputs
  languages.py         CEFR parsing and comparison
  validators/          facts.py (fabrication gate), aliases.py (scoped umbrella terms)
  evidence.py          Chroma + bge-m3 evidence store, cross-encoder rerank
  render.py            Jinja2 → headless Chrome PDF
  scoring/ats.py       ATS score fact table
  section_order.py     fixed section order per role type
  costs.py, budget.py  per-call token/cost ledger, daily cap
  runner.py, batch.py  multi-board polling, Batches API mode
  sourcing/            adzuna, arbeitnow, personio, greenhouse, lever, ashby, linkedin_apify
  mcp/server.py        Claude Desktop tools
  db/                  SQLite (stdlib sqlite3): jobs, runs, applications
prompts/               agent_cv, analyze, rewrite, requirements, extract_profile
templates/             cv_two_column.html, cover_letter.html
data/                  master_profile.yaml, cv_data.json, preferences.yaml (none committed)
evals/                 golden + real JD fixtures, run_harness.py
scripts/               profile_sync.py, compare_paths.py, build_cv_data.py
```

## Testing

```bash
pytest -q && ruff check . && mypy src
pytest evals/                          # golden set
```

## Docs

- `CLAUDE.md` — design decisions that must not be undone, and why.
- `plan.md` — rubrics and thresholds (§ references in code).
- `Bugs.md`, `improvement.md` — history of production fixes.
- The full multi-node pipeline (extract / diagnose / review / recruiter_sim / hiring_manager)
  lives on `main`.
