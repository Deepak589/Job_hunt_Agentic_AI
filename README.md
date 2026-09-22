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
- **No resumability / crash recovery** — every `run()`/`run_many()`/`add --review` now
  opens an `AsyncSqliteSaver` checkpointer (WAL mode, `durability="sync"`), thread id =
  job id. A paused review survives a process restart (`jobpilot review <id>` resumes
  from disk); a crash mid-`add` is picked back up with `jobpilot resume <id>`, which
  replays only the nodes that hadn't completed — `extract_requirements`/`diagnose`/
  `rewrite` aren't re-paid for.
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
  approve/**edit**/reject. Edit opens the draft as YAML in `$EDITOR`, re-validates on
  save, and resumes the graph with the new draft via a `human_review` node
  (`interrupt()`/`Command(resume=...)`, replacing the old static
  `interrupt_before=["render_documents"]`). This routes back through
  `recruiter_sim`/`hiring_manager` for a fresh verdict and pauses again — they no
  longer go stale against an edited draft the way they used to.
- **Retrieval precision was single-signal (cosine only)** — added cross-encoder
  reranking (`evidence.rerank()`) as the threshold comment's own documented
  upgrade path, then *measured* it with `jobpilot index calibrate` instead of
  assuming it helps: on the 15-probe set, cosine top-1 is correct 15/15,
  cross-encoder top-1 is correct 14/15. The coverage gate stays on cosine —
  reranking is exposed (`Evidence.rerank_score`) but not wired into the gate,
  because the data says it would make coverage worse, not better.
- **No latency/tracing visibility** — optional Langfuse tracing wired into every
  graph invocation (see "Tracing" below). No-op unless configured.
- **Live ingestion covered one job board, not a real pipeline** — `runner.py` +
  `sourcing/` now poll multiple ATS boards (`personio`, `greenhouse`, `lever`,
  `ashby`, plus `adzuna`/`arbeitnow`) off a single `config/companies.yaml`,
  dedupe against `db/repo.py`'s job history so a re-poll costs nothing on
  unchanged postings, and are meant to run unattended via `jobpilot run`
  (`ops/com.jobpilot.run.plist.template` — a scheduler launchd/cron template,
  not a hosted service). Three consecutive fetch failures for one company are
  logged and surfaced in the digest instead of failing silently.
- **Batch mode was one JD at a time even for a nightly backlog** — `jobpilot run
  --batch` submits `extract_requirements`+`diagnose` for every new posting
  through the Anthropic Batches API (`batch.py`) instead of live calls: ~50%
  cheaper, tool-forced structured output (the Batches API doesn't support the
  `json_schema` method the live path uses), then prefills those two nodes into
  `run_many()` so the rest of the graph doesn't redo LLM calls the batch already
  paid for. Falls back to a live call per-job if a batch entry errored.
- **CV claims for "built X myself" projects had no source of truth beyond the
  hand-maintained profile** — `repo_docs.py` fetches a GitHub repo's README +
  top-level `docs/*.md` (unauthenticated REST API, SHA-cached so unchanged repos
  aren't re-fetched) and chunks them by heading into the evidence store
  alongside CV bullets, so `jobpilot index build` can cite a repo's own docs as
  evidence for a requirement, not just what's already written in
  `master_profile.yaml`.
- **No feedback loop after applying — outcomes were never recorded or reused**
  — `jobpilot applied <id>` logs that a tailored CV was sent (keyed to the
  rendered PDF's content hash, so a re-render is distinguishable from the one
  actually submitted); `jobpilot outcome <id> interview|reject|ghost` records
  what happened. `jobpilot judge-stats` computes Cohen's kappa (`judgestats.py`)
  between each judge node's verdict (`review`/`recruiter_sim`/`hiring_manager`)
  and the human's actual review action, so agreement is measured, not assumed —
  `ats_score()`'s review-score cap is skipped when kappa is too low with enough
  history to trust it, so a judge that's drifted from human judgement stops
  quietly warping the score.
- **A human's edit during `jobpilot review` was thrown away after that one run**
  — `preferences.py` diffs the pre/post-edit draft, records the changed bullets
  to `data/preferences.yaml` (deduped on overlap), and `rewrite` now includes a
  "user preferences" block in its prompt when one exists — so a correction made
  once (e.g. "don't call it 'led', I was one of three") persists into future
  drafts instead of getting silently reverted next run.
- **ATS score could drift when a rewritten draft reordered CV sections** — new
  `test_invariance.py` pins `ats_total` and `recruiter_sim`'s result to be
  identical regardless of section order, catching a scoring bug the existing
  per-component tests wouldn't have (they never varied section order).

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
jobpilot resume <id>                   # continue a plain 'add' that crashed mid-run
jobpilot add --dir jds/                # run every .txt JD in a directory concurrently

jobpilot index build [--force]         # (re)build the evidence store from master_profile.yaml
jobpilot index calibrate               # measure SEM_THRESHOLD against labeled probes

jobpilot profile check                 # validate master_profile.yaml integrity

jobpilot cost                          # all-time spend summary from the runs ledger
jobpilot cost --days 7                 # spend summary for the last 7 days

jobpilot applied <id>                  # record that the tailored CV was sent
jobpilot outcome <id> interview        # record what happened: interview | reject | ghost
jobpilot judge-stats                   # Cohen's kappa: judge verdicts vs human review actions

jobpilot source arbeitnow --query "Werkstudent" --location Berlin   # no auth required
jobpilot source arbeitnow --query "Data Scientist" --run            # fetch + run each JD through the pipeline
jobpilot source adzuna --query "Data Scientist" --location Berlin   # requires ADZUNA_APP_ID/ADZUNA_APP_KEY

jobpilot run                           # poll config/companies.yaml, run genuinely new postings, print a digest
jobpilot run --batch                   # same, but extract+diagnose via the Batches API (~50% cheaper, slower)
jobpilot run --digest json             # machine-readable digest (for the scheduler in ops/)
```

`config/companies.yaml` lists companies to poll on ATS boards that don't need
credentials (`personio`, `greenhouse`, `lever`, `ashby` — matches
`runner._CONNECTORS`). `jobpilot run` is meant to be driven by a scheduler, not
run ad hoc — see `ops/README.md` and `ops/com.jobpilot.run.plist.template`.

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
  runner.py          multi-company polling, dedupe, batch prefill, notify-on-failure
  batch.py            Anthropic Batches API client (nightly/cheap mode)
  nodes/             requirements, diagnose, rewrite, validate_facts, review
  sourcing/           one connector per job board (adzuna, arbeitnow, personio,
                       greenhouse, lever, ashby, linkedin_apify) + base.py's shared
                       JobSource protocol
  scoring/ats.py      ATS score fact-table + arithmetic
  validators/facts.py fabrication gate
  evidence.py         Chroma-backed semantic evidence store
  repo_docs.py         GitHub README/docs.md ingestion into the evidence store
  preferences.py       procedural memory from human edits during review
  judgestats.py        Cohen's kappa — judge verdict vs human review action
  profile.py          master_profile.yaml loader/validator
  render.py            Typst PDF rendering
  db/repo.py            SQLite persistence: jobs, runs, applications, judgements
data/master_profile.yaml   source of truth: bullets, skills, evidence
data/preferences.yaml      learned bullet-level corrections from past edits
config/companies.yaml      companies to poll for `jobpilot run`
ops/                        scheduler template for `jobpilot run`
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
