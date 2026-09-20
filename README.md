# JobPilot

Multi-agent job search and CV tailoring pipeline. Run a job description through a
LangGraph pipeline (requirements → diagnosis → rewrite → fact validation → review →
recruiter sim → hiring manager → render + ATS score) and get a gap report, tailored
CV/cover-letter PDFs, and a computed ATS score.

## Pipeline

1. **Requirements** — extract hard/soft/disqualifier requirements from the JD.
2. **Diagnosis** — match requirements against evidence in your profile; flag gaps
   and positioning mismatches.
3. **Rewrite** — draft tailored bullets/profile line, only if no blocking gap.
4. **Fact validation** — reject any claim (skill, number, employer) with no evidence
   in `master_profile.yaml`.
5. **Review** — score the draft against keyword coverage, quantification, tone.
6. **Recruiter sim** — shallow keyword-literal ATS screen over hard requirements
   only. Hard-fail ends the run before render (no wasted render/score on a dead job).
7. **Hiring manager** — deeper fit/tone/defensibility read; informational, doesn't
   gate the graph.
8. **Render + ATS score** — typeset CV/cover letter (Typst → PDF) and compute the
   ATS score from a counted fact table (`src/agentic_ai/scoring/ats.py`).

Skip renders with `add --no-render` to stop after the gap report. Pause before
render for a human check with `add --review`, then `jobpilot review <id>` to
approve/reject.

## Production fixes

Issues found running this for real, and what fixed them (full detail in
`improvement.md`):

- **No cost visibility** — every LLM call now logged (`costs.py`): tokens in/out,
  `$` cost per node, running `total_cost_usd` on the job. Printed by the CLI.
- **Cache savings invisible** — `rewrite`/`diagnose` cache the profile block
  (Anthropic prompt caching); `costs.py` prices cache read/write at 0.1x/1.25x
  instead of flat rate, so the cost number reflects what caching actually saves.
- **Redundant re-computation** — `classify_role` re-ran on every rewrite retry;
  now `lru_cache`d by JD text.
- **No resumability / crash recovery** — added a `SqliteSaver` checkpointer with
  `interrupt_before=["render_documents"]`. A paused review survives a process
  restart; `jobpilot add --review` / `jobpilot review <id>` resume from disk.
- **No run history** — `db/repo.py` persists every `add`/`review` run (tokens,
  cost, coverage, verdict) to SQLite, not just stdout.
- **Review score could be gamed** — `review_score` wasn't wired into `ats_score()`
  at all; now a low review score caps `total` at 94, so a slick draft can't cross
  the apply threshold on wording alone.
- **No pre-render screen** — added `recruiter_sim` (role 4) so a hard-requirement
  gap fails fast, before spending a render + ATS pass on a job that can't clear
  the client's own ATS keyword filter.
- **No spend visibility or cap across runs** — `jobpilot cost` reads the runs
  ledger back out (per-day + all-time tokens/$); `JOBPILOT_MAX_DAILY_COST_USD`
  stops `jobpilot add` before it spends once today's total hits the cap. Every
  run also appends a JSON line (`data/jobpilot.log.jsonl`) with its per-node cost
  breakdown, verdict, and skip reason, separate from the Rich console output.
- **No rate-limit/backoff handling** — `langchain_anthropic.ChatAnthropic` already
  retries 429/5xx with backoff via `max_retries`; made it configurable
  (`Settings.llm_max_retries`, default 3) through a shared `llm.make_llm()`
  factory all 6 nodes use instead of constructing `ChatAnthropic` directly.
- **One JD per invocation, no batching** — `jobpilot add --dir <path>` runs every
  `.txt` file in a directory concurrently via `graph.run_many()`
  (`asyncio.gather`, bounded by `Settings.max_concurrent_jobs`, default 3). The
  single-JD `--file`/`--stdin` path is unchanged.
- **No live job-board ingestion** — `jobpilot source arbeitnow` (no auth, live-
  verified) and `jobpilot source adzuna` (needs `ADZUNA_APP_ID`/`ADZUNA_APP_KEY`)
  fetch real postings; `--run` pipes each straight through the same pipeline as
  `jobpilot add`.
- **Review pause was approve/reject only** — `jobpilot review <id>` now offers
  approve/**edit**/reject. Edit opens the draft as YAML in `$EDITOR`, re-validates
  on save, and writes it into the LangGraph checkpoint before resuming.
  `recruiter`/`hiring_manager` are deliberately NOT re-run after an edit — they
  still reflect the pre-edit draft.
- **Retrieval precision was single-signal (cosine only)** — added cross-encoder
  reranking (`evidence.rerank()`) as the threshold comment's own documented
  upgrade path, then *measured* it with `jobpilot index calibrate` instead of
  assuming it helps: on the 15-probe set, cosine top-1 is correct 15/15,
  cross-encoder top-1 is correct 14/15. The coverage gate stays on cosine —
  reranking is exposed (`Evidence.rerank_score`) but not wired into the gate,
  because the data says it would make coverage worse, not better.
- **No latency/tracing visibility** — optional Langfuse tracing wired into every
  graph invocation (see "Tracing" below). No-op unless configured.

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
jobpilot add --file jd.txt --review    # pause before render; run 'jobpilot review <id>' to continue
jobpilot review <id>                   # approve (render) / edit (fix a bullet first) / reject
jobpilot add --dir jds/                # run every .txt JD in a directory concurrently

jobpilot index build [--force]         # (re)build the evidence store from master_profile.yaml
jobpilot index calibrate               # measure SEM_THRESHOLD against labeled probes

jobpilot profile check                 # validate master_profile.yaml integrity

jobpilot cost                          # all-time spend summary from the runs ledger
jobpilot cost --days 7                 # spend summary for the last 7 days

jobpilot source arbeitnow --query "Werkstudent" --location Berlin   # no auth required
jobpilot source arbeitnow --query "Data Scientist" --run            # fetch + run each JD through the pipeline
jobpilot source adzuna --query "Data Scientist" --location Berlin   # requires ADZUNA_APP_ID/ADZUNA_APP_KEY
```

Set `JOBPILOT_MAX_DAILY_COST_USD` (env or `.env`) to cap daily spend — `jobpilot add`
checks today's total against it before running the graph and refuses (exit 1, no LLM
calls made) once the cap is hit. Unset (default) means no cap.

## Tracing (optional)

Set these to get a Langfuse trace (latency, inputs/outputs) per graph node run:

```bash
JOBPILOT_LANGFUSE_PUBLIC_KEY=pk-...
JOBPILOT_LANGFUSE_SECRET_KEY=sk-...
JOBPILOT_LANGFUSE_HOST=https://cloud.langfuse.com   # optional, defaults to Langfuse's own default
```

No-op when unset (the default) — no import, no network call, behavior unchanged. Requires
the `tracing` extra: `uv sync --extra tracing`.

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

### Eval harness (regression detection for scoring/retrieval changes)

There's no ground-truth expected output for the JDs in `evals/golden/` and
`evals/real/` — `evals/run_harness.py` instead snapshot-tests coverage metrics
(`hard_coverage`, `soft_coverage`, `semantic_fit`, `skip_reason`) against a
checked-in baseline (`evals/baseline.json`), so a change to `evidence.py`'s
threshold or `ats.py`'s rubric shows up as a fixture diff instead of a manual
eyeball. It costs real Anthropic API money (4 LLM calls per fixture), so the
default fixture set is `evals/golden/` (4 files) and `evals/real/` is opt-in.

```bash
python evals/run_harness.py --check                 # diff evals/golden/ against the baseline
python evals/run_harness.py --fixtures all --check    # also cover evals/real/ (10 more files)
```

After an *intentional* scoring/retrieval change, review the diff it prints, then
deliberately move the baseline:

```bash
python evals/run_harness.py --update-baseline
```

`--full` additionally runs recruiter_sim/hiring_manager/render and captures
`ats.total`/`ats.verdict` (more LLM calls; `ats_total` gets a ±5 tolerance since it's
downstream of LLM-written bullets and isn't perfectly deterministic run to run).

See `CLAUDE.md` for the full agent workflow spec (five-role pipeline, ATS scoring
rubric, CV bullet style) and `tasks/todo.md` for open items.
