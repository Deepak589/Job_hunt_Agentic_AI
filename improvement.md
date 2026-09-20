# improvement.md — deferred until all phases (1-3) are done

Not a bug list — things genuinely not built yet. Grounded in what's actually in the
code (checked `config.py`, `graph.py`, `evidence.py`, `coverage.py`, `nodes/*`,
`validators/facts.py`), not aspirational. Cross-referenced against open items
already logged in `tasks/todo.md` so nothing is duplicated.

## 1. Cost / token accounting
- ~~No token usage or $ cost logged anywhere~~ — **fixed 2026-09-16**: `costs.py`
  records `{node, model, input_tokens, output_tokens, cost_usd}` per LLM call,
  `JobState.total_cost_usd`, printed by the CLI. `classify_role` deliberately
  excluded (cached + cheap, see inline note).
- ~~No Anthropic prompt caching~~ — **fixed 2026-09-16**: `rewrite` (2 breakpoints)
  and `diagnose` (1 breakpoint) cache the profile block; `costs.py` prices cache
  read/write at the correct 0.1x/1.25x rates. **Live-confirmed 2026-09-18** — and
  the confirmation run found 2 real bugs the mocked tests couldn't catch:
  1. `costs.py` priced every cache-write token as plain base input. Root cause:
     `langchain_anthropic` 1.5.6 reports a 5-min ephemeral cache write under
     `input_token_details.ephemeral_5m_input_tokens`, not the generic
     `cache_creation` key `record_usage` was reading — confirmed via raw
     `usage_metadata` dumps against the real API. Fixed: `record_usage` now falls
     back to `ephemeral_5m_input_tokens + ephemeral_1h_input_tokens` when
     `cache_creation` is 0. Regression test added in `tests/test_costs.py`.
  2. The CLI's own `[cache: N read, M written]` tag never rendered, no error either
     — literal `[...]` is Rich markup syntax, so `console.print()` silently
     swallowed it as an unrecognized style tag. Fixed by escaping the bracket in
     `cli.py`.
  Live proof (`evals/real/02_mubea_werkstudent_ds.txt` run twice back-to-back):
  `diagnose` read 5471 cached tokens, `rewrite` wrote 2395 then read 9460 on its
  retry. 125/125 tests pass.
- ~~`classify_role` re-runs on every rewrite retry~~ — **fixed 2026-09-16**: now
  `lru_cache`d by `jd_text` in `section_order.py`.

## 2. Latency / concurrency
- ~~No timing/tracing around node execution~~ — **fixed 2026-09-18**: optional
  Langfuse tracing wired into every `graph.invoke`/`ainvoke` call site
  (`graph._tracing_callbacks()`). No-op — no import, no network — unless
  `JOBPILOT_LANGFUSE_PUBLIC_KEY`/`_SECRET_KEY` are both set. Requires the
  `tracing` extra (`uv sync --extra tracing`).
- ~~Fully sequential: one JD per `jobpilot add` invocation~~ — **fixed 2026-09-18**:
  `graph.run_many()` runs multiple JDs concurrently via `ainvoke` + `asyncio.gather`,
  bounded by `Settings.max_concurrent_jobs` (default 3). `jobpilot add --dir <path>`
  runs every `.txt` file in a directory this way. The single-JD `--file`/`--stdin`
  path is unchanged.
- ~~No rate-limit/backoff wrapper around Anthropic calls~~ — **fixed 2026-09-18**:
  turned out `langchain_anthropic.ChatAnthropic` already forwards `max_retries` to
  the underlying SDK client, which retries 429/5xx with exponential backoff out of
  the box — no tenacity wrapper needed. Made it configurable
  (`Settings.llm_max_retries`, default 3) via a shared `llm.make_llm()` factory all
  6 nodes now use instead of constructing `ChatAnthropic` directly.

## 3. Persistence / resumability
- ~~No SQLite checkpointer~~ — **fixed 2026-09-16** (Phase 3): `SqliteSaver` +
  `interrupt_before=["render_documents"]`, `jobpilot add --review` /
  `jobpilot review <id>`. See `tasks/todo.md`.
- ~~No run history / cost ledger across jobs~~ — **fixed 2026-09-16**: `db/repo.py`
  persists `jobs` + `runs` (cost ledger) on every `add`/`review` run.
  ~~A `jobpilot cost` reporting command over this data is not built yet~~ — **fixed
  2026-09-18**: `jobpilot cost [--days N]` prints a per-day + all-time spend table
  off the same `runs` table (`db/repo.cost_summary()`).
- ~~No spending cap~~ — **fixed 2026-09-18**: `Settings.max_daily_cost_usd`
  (`JOBPILOT_MAX_DAILY_COST_USD`); `cli._check_budget()` refuses `jobpilot add`
  before any LLM call if today's spend already hit the cap.
- ~~No structured logs~~ — **fixed 2026-09-18**: one JSON line per run (and per
  budget-guard rejection) appended to `settings.log_path`
  (`data/jobpilot.log.jsonl`) — per-node cost breakdown, total cost, verdict,
  skip_reason. Fully decoupled from the Rich console output.
- ~~"edit" in the review flow not implemented (only y/n)~~ — **fixed 2026-09-18**:
  `jobpilot review <id>` now offers approve/edit/reject. Edit dumps the draft to
  YAML, opens `$EDITOR`, re-validates on save, and writes it into the LangGraph
  checkpoint via `graph.update_draft()` before resuming. Known gap, deliberately
  out of scope: `recruiter`/`hiring_manager` verdicts are NOT re-run after an edit
  — they still reflect the pre-edit draft.

## 4. Guardrail gaps
- ~~`review_score` isn't wired into `ats_score()` at all~~ — **fixed 2026-09-16**:
  `review_score < min_review_score` now caps `total` at 94 (held out of `apply`).
  See `plan.md` §6 "Review-score cap", `tasks/todo.md`.
- "Literal keywords" ATS component is binary 0/20 — most components are easy
  near-max once a draft clears the fabrication gate; only hard-requirement coverage
  showed real spread across the 3 live runs tested.
- ~~No cross-encoder reranking on retrieval~~ — **implemented 2026-09-18**:
  `evidence.rerank()` rescores top-k candidates with a cross-encoder
  (`cross-encoder/ms-marco-MiniLM-L-6-v2`), exposed as `Evidence.rerank_score`
  without touching `similarity`. **Measured, not assumed**: `jobpilot index
  calibrate` now reports both — on the 15-probe set, cosine top-1 is correct
  15/15, cross-encoder top-1 is correct 14/15 (rerank changed the top pick on 7/15
  probes, once for the worse). Recommendation: **do not** switch the coverage gate
  to `rerank_score` — the data says cosine is currently better, confirming this
  file's own note that named tech is carried by the keyword half, not semantic.
  `sem_threshold` also left unchanged for the same reason.

## 5. Five-role pipeline (Phase 3, plan.md §16) — DONE 2026-09-16
- ~~`recruiter_sim` node not built~~ — done, `nodes/recruiter_sim.py`.
- ~~`hiring_manager` node not built~~ — done, `nodes/hiring_manager.py`.
- ~~Human interrupt point (§9)~~ — done, `run_for_review`/`resume_review` +
  `jobpilot add --review` / `jobpilot review`. See `tasks/todo.md`.

## 6. Dead / unwired state
- ~~`highlighted_projects` read by nothing~~ — **fixed 2026-09-16**: printed in the
  CLI's DRAFT report line (not wired into render/ats — kept for Phase 3 to decide).
- ~~`fact_gate`/`review_gate` typing drift~~ — **fixed 2026-09-16**: `review_gate`
  now returns `Literal["retry", "proceed"]`.

## 7. Job sourcing
- ~~`Job.source` supports `adzuna | arbeitnow | manual`, but only `manual` is
  implemented~~ — **fixed 2026-09-18**: `jobpilot source arbeitnow` (no auth,
  live-verified against the real API) and `jobpilot source adzuna` (requires
  `ADZUNA_APP_ID`/`ADZUNA_APP_KEY`, not live-tested — no credentials available,
  tested against mocked HTTP responses shaped from Adzuna's documented API).
  Both take `--run` to pipe fetched postings straight through `run()`/`_report()`
  like `jobpilot add`, or print a picker table without it.

## 8. Testing / eval gaps
- ~~`evals/real/` and `evals/golden/` exist as fixtures but there's no automated
  harness~~ — **fixed 2026-09-18**: `evals/run_harness.py` snapshot-tests
  `hard_coverage`/`soft_coverage`/`semantic_fit`/`skip_reason` (plus `ats_total`
  with `--full`) against a checked-in `evals/baseline.json`, `--check` to diff,
  `--update-baseline` to deliberately move it after reviewing a real scoring
  change. No ground-truth expected output exists for these JDs, so this is
  regression detection (did this fixture's output change), not accuracy scoring.
