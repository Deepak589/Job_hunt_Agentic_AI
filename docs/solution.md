# Phase 4 — Real-time hardening (solution to docs/system_design_review_2026-09-18.md)

Same format as `tasks/todo.md`: step → files → verify. Do in order. Each step is one commit, tests green before next.
Numbers in [ ] refer to review sections.

## Step 1 — stop money leaks (review 2.1, 2.2)
- [ ] `state.py` — `Job.id` = `sha256(source + ":" + (url or slug))[:16]`; add `Job.content_hash = sha256(jd_text)`. Manual `--file` keeps content hash as id (no url).
- [ ] `db/schema.sql` — `jobs.content_hash TEXT`, `jobs.raw_json TEXT`; index on `(id)`.
- [ ] `db/repo.py` — `already_processed(job_id, content_hash) -> bool` (a run row exists with same content_hash and verdict not null).
- [ ] `cli.py source --run` + `graph.run_many` — skip when `already_processed`; log `{"event":"skip_seen"}`.
- [ ] `graph.run_many._run_one` — call `_check_budget()` **inside** the semaphore, before `ainvoke`. Move `_check_budget` from `cli.py` to `budget.py` so graph can import it.
- [ ] `budget.py` — reserve in-flight spend: `reserved += est_cost` on claim, release on finish; cap check = `persisted + reserved`.
      → verify: `tests/test_budget.py` — 5 jobs, cap = 2×avg, exactly 2 run, 3 logged `budget_guard_rejected`. Re-running same arbeitnow page twice = 0 new runs.

## Step 2 — durability (review 2.3)
- [ ] `graph.py` — `build_graph()` always takes a checkpointer; `run()`/`run_many()` open ONE `SqliteSaver` per process (module-level, `PRAGMA journal_mode=WAL`), `thread_id = job.id`.
- [ ] `graph.py` — compile with `durability="sync"`.
- [ ] Replace `interrupt_before=["render_documents"]` with a `human_review` node: `decision = interrupt({"draft": state.draft, "recruiter": ..., "hiring_manager": ...})`; returns `{"draft": decision["draft"]}` on edit.
- [ ] `resume_review` → `graph.invoke(Command(resume={"action": "approve"|"edit"|"reject", "draft": ...}), config)`.
- [ ] After an edit, route `human_review → recruiter_sim` again (fixes stale verdict noted in README).
- [ ] `render.py` — idempotent: if `cv.pdf` exists and `draft` hash in `out/<id>/draft.sha` matches, skip compile.
      → verify: kill -9 during `hiring_manager` → `jobpilot resume <id>` continues without re-paying extract/diagnose/rewrite (assert `llm_calls` count unchanged). Edit at review → recruiter re-run appears in notes.

## Step 3 — hot-path waste (review 2.4, 2.5)
- [ ] `evidence.retrieve_many(..., rerank: bool = False)`; only `calibrate()` passes `True`.
- [ ] `profile.Profile.load` — `lru_cache` keyed on `(path, mtime_ns)`.
- [ ] `render.py` — temp JSON via `tempfile.NamedTemporaryFile`, not `data/`.
- [ ] `cli.py --dir` — exit 0 unless a job *errored*; skips are normal.
      → verify: `time jobpilot add --file evals/real/02_... --no-render` before/after; expect >1s drop (cross-encoder gone). `Profile.load` called once per run (mock + call count).

## Step 4 — ATS score means something (review 3.4)
- [ ] `scoring/ats.py` — `parse_pdf` returns extracted text; new component:
      `literal keywords = 20 · |{kw ∈ evidenced JD terms : kw in pdf_text}| / |evidenced JD terms|`.
- [ ] Drop `positioning` → fold 5 pts into `hard req coverage` (55) or make it "profile_line contains ≥1 hard keyword".
- [ ] `plan.md §6` — update rubric table to match (CLAUDE.md says formula lives there).
- [ ] `evals/run_harness.py --update-baseline` after, with the diff reviewed.
      → verify: `tests/test_ats_score.py` — draft A (keywords in PDF) vs draft B (same coverage, keywords absent) must differ by ≥10 pts. Today they score identical.

## Step 5 — LLM plumbing (review 3.2, 3.3)
- [ ] `llm.py` — `make_structured(model, schema)` → `with_structured_output(schema, method="json_schema", include_raw=True)`. Confirm via raw request log that `output_config.format` is sent; if not, call `anthropic.Anthropic().messages.parse()` directly.
- [ ] Nodes — retry loop only when `stop_reason in ("max_tokens", "refusal")`; drop parse-retry.
- [ ] `rewrite.py`, `diagnose.py` — profile-block breakpoint `{"type":"ephemeral","ttl":"1h"}`; keep 5m on per-job blocks.
- [ ] `costs.py` — price 1h writes at 2.0×; warn (structlog) when a node with `cache_control` reports `cache_read == 0 and cache_creation == 0` (prefix below min tokens → silent no-cache).
- [ ] `section_order.classify_role` — record usage (it's a paid call).
      → verify: two runs 20 min apart → second `diagnose` shows `cache_read > 0`. Parse-retry counter in `llm_calls` = 0 over `evals/golden/`.

## Step 6 — ingestion + scheduler (review 2.6, 4.1)
- [ ] `sourcing/base.py` — `JobSource` Protocol: `fetch(since) -> list[dict]`, `normalize(raw) -> Job`.
- [ ] `sourcing/personio.py` (XML, `{co}.jobs.personio.de/xml?language=en`), `greenhouse.py`, `lever.py`, `ashby.py`. `config/companies.yaml` with 20–30 Berlin targets.
- [ ] `sourcing/arbeitnow.py` — paginate until `created_at < since`; `lang` via `lingua` (drop stopword hack).
- [ ] `db/schema.sql` — `company_snapshots(company, ats, posting_ids, fetched_at, consecutive_failures)`.
- [ ] `runner.py` (plan §19) — `jobpilot run --digest json`: poll all sources → diff → prefilter (§17: lang, location, employment_type; 0 tokens) → queue → `run_many` under `LimitGuard` → digest.
- [ ] Scheduler: Cowork scheduled task (plan §20) or `launchd` on the Mac, hourly, calling `jobpilot run`.
- [ ] Remove unused deps or use them: `trafilatura`/`extruct` in `jobpilot add <url>` (JSON-LD `JobPosting` first).
      → verify: `jobpilot run` twice in a row → second run: 0 LLM calls, digest says "0 new". Kill a company's endpoint → after 3 runs `consecutive_failures=3` and a notify line.

## Step 7 — Batches API for the nightly queue (review 4.2)
- [ ] `llm.py` — `BatchClient`: submit extract+diagnose for all queued jobs in one `messages.batches.create`, poll, fan results back into `JobState`. Rewrite/review stay live (need the retry loop).
- [ ] `runner.py --batch` flag; interactive `jobpilot add` unchanged.
      → verify: `jobpilot cost` shows per-job cost ~halved on a 10-job night vs live path.

## Step 8 — close the loop (review 2.7, 4.4)
- [ ] `db/schema.sql` — `applications(job_id, sent_at, cv_pdf_sha, outcome, outcome_at)`; `jobpilot applied <id>`, `jobpilot outcome <id> interview|reject|ghost`.
- [ ] `db/schema.sql` — `judgements(run_id, node, score_or_verdict, human_action)`; write on every review approve/edit/reject.
- [ ] `jobpilot judge-stats` — Cohen's κ per judge node vs human action.
- [ ] Review-edit diff → `data/preferences.yaml` (plan §21.4), appended to rewrite prompt as `<user_preferences>` (human-approved lines only).
- [ ] `tests/test_invariance.py` — same draft, two section orders → `ats.total` equal, `recruiter.result` equal.
      → verify: after 30 applications, `judge-stats` prints κ; `preferences.yaml` has ≥1 line derived from an edit.

## Step 9 — judges (review 3.1) — only after Step 8 has data
- [ ] `recruiter_sim` → deterministic: hard keywords ∩ `pdf_text`; hard_fail if any hard keyword with evidence is missing from PDF. Delete the Haiku call.
- [ ] `review` → 3 samples (or Haiku+Sonnet) → weighted majority vote; weights from disagreement (Zhang et al. 2026) once κ data exists, plain majority before.
- [ ] If κ(review) < 0.4 after 30 runs → remove the review cap from `ats_score`.
      → verify: recruiter node has 0 `llm_calls`; `review` variance across 3 samples logged.

## Step 10 — evidence store (review 3.6)
- [ ] Either: `evidence/repo_docs.py` ingests README + `docs/*.md` from `master_profile.repos[]` (chunk by heading, SHA-cached) — closes "built agents yourself" gaps.
- [ ] Or: replace Chroma with `numpy` cosine over 20 vectors saved as `.npy`. Pick one; don't keep Chroma for 20 rows.
      → verify: Retorio JD (`evals/real/07`) hard gap "built agents yourself" becomes covered by a `repo_doc` evidence chunk.

## Done means
- `jobpilot run` hourly for 7 days: 0 duplicate runs, spend ≤ cap every day, 0 lost runs on crash.
- ATS score separates two drafts with equal coverage.
- κ for each judge node is a number in the repo, not an assumption.
