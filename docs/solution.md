# Phase 4 — Real-time hardening (solution to docs/system_design_review_2026-09-18.md)

Same format as `tasks/todo.md`: step → files → verify. Do in order. Each step is one commit, tests green before next.
Numbers in [ ] refer to review sections.

## Step 1 — stop money leaks (review 2.1, 2.2) — DONE
- [x] `state.py` — `Job.id` = `sha256(source + ":" + (url or slug))[:16]`; add `Job.content_hash = sha256(jd_text)`. Manual `--file` keeps content hash as id (no url).
- [x] `db/schema.sql` — `jobs.content_hash TEXT`, `jobs.raw_json TEXT`; index on `runs(job_id)` (the column `already_processed`/`cost_summary` actually filter on — `jobs.id` already had its PK index). `db/repo.init_db` ALTER-TABLEs these into a pre-existing on-disk db.
- [x] `db/repo.py` — `already_processed(job_id, content_hash) -> bool` (a run row exists with same job_id **and** content_hash, verdict not null) — `runs.content_hash` is written at persist time so a later re-fetch of `jobs.content_hash` can't retroactively change what an old run matched.
- [x] `cli.py source --run` (`_run_and_report`) + `graph.run_many` — skip when `already_processed`; log `{"event":"skip_seen"}`.
- [x] `graph.run_many._run_one` — call `_check_budget()` **inside** the semaphore, before `ainvoke`. Move `_check_budget` from `cli.py` to `budget.py` so graph can import it. (Landed in dbec0d1.)
- [x] `budget.py` — reserve in-flight spend: `reserved += est_cost` on claim, release on finish; cap check = `persisted + reserved`. (Landed in dbec0d1.)
      → verified: `tests/test_run_many.py` (budget + dedupe), `tests/test_sourcing_{arbeitnow,adzuna}.py` (id now source+url; content_hash still matches a manual paste of the same text), `tests/test_db_repo.py`, `tests/test_graph_phase3.py` — 164/164 passing.

## Step 2 — durability (review 2.3) — DONE
- [x] `graph.py` — `build_graph()` takes a checkpointer; `run()`/`run_many()`/`run_for_review()`/`resume_review()` each open ONE `AsyncSqliteSaver` per call (WAL mode via `_open_checkpointer()`), `thread_id = job.id`. (Async, not sync `SqliteSaver` — a sync saver blocks the event loop under `run_many`'s concurrent `ainvoke`s.)
- [x] `graph.py` — invoke with `durability="sync"` (this langgraph version takes `durability` on `ainvoke`, not `compile`).
- [x] Replaced `interrupt_before=["render_documents"]` with a `human_review` node: `decision = interrupt({"draft": ..., "recruiter": ..., "hiring_manager": ...})`; returns `{"draft": Draft.model_validate(decision["draft"])}` on edit, `{"skip_reason": ...}` on reject. Only wired in when `build_graph(review_pause=True)` — automated batch runs never pause.
- [x] `resume_review(job_id, action, draft=None)` → `graph.ainvoke(Command(resume={"action": ..., "draft": ...}), config, durability="sync")`. `action="reject"` still never re-invokes the graph (reads the snapshot, sets `skip_reason` locally) — cheapest path, matches old behavior.
- [x] After an edit, `human_review` routes back to `recruiter_sim` (fixes stale verdict noted in README) — pauses again with a fresh verdict instead of rendering the old one.
- [x] `render.py` — idempotent: `out/<id>/draft.sha` holds the last-rendered draft's hash; `render_documents` skips both Typst compiles when `cv.pdf`/`cover_letter.pdf`/`draft.sha` all exist and the hash matches.
- [x] `resume(job_id)` (new, closes the crash-recovery loop the verify line needs) — probes with the `review_pause=True` (superset) topology to tell a crashed plain run apart from one genuinely paused for review, then replays only uncompleted nodes via `graph.ainvoke(None, config)`.
      → verified: `tests/test_graph_phase3.py::test_resume_continues_without_repaying_completed_nodes` — `hiring_manager` raises once (simulated crash), `resume()` re-runs only `hiring_manager` (2 calls); `extract_requirements`/`diagnose`/`rewrite` stay at 1 call each. `test_resume_review_edit_reruns_recruiter_and_hiring_manager` — edit → `recruiter_sim` call count goes 1→2, new pause carries the edited draft. `tests/test_render.py::test_render_documents_skips_compile_when_draft_unchanged`.

## Step 3 — hot-path waste (review 2.4, 2.5) — DONE
- [x] `evidence.retrieve_many(..., rerank: bool = False)`; only `calibrate()` passes `True`.
- [x] `profile.Profile.load` — `lru_cache` keyed on `(path, mtime_ns)`.
- [x] `render.py` — temp JSON via `tempfile.NamedTemporaryFile`, not `data/`.
- [x] `cli.py --dir` — exit 0 unless a job *errored*; skips are normal.
      → verified: ce358f0.

## Step 4 — ATS score means something (review 3.4)
- [ ] `scoring/ats.py` — `parse_pdf` returns extracted text; new component:
      `literal keywords = 20 · |{kw ∈ evidenced JD terms : kw in pdf_text}| / |evidenced JD terms|`.
- [ ] Drop `positioning` → fold 5 pts into `hard req coverage` (55) or make it "profile_line contains ≥1 hard keyword".
- [ ] `plan.md §6` — update rubric table to match (CLAUDE.md says formula lives there).
- [ ] `evals/run_harness.py --update-baseline` after, with the diff reviewed.
      → verify: `tests/test_ats_score.py` — draft A (keywords in PDF) vs draft B (same coverage, keywords absent) must differ by ≥10 pts. Today they score identical.

## Step 5 — LLM plumbing (review 3.2, 3.3) — DONE
- [x] `llm.py` — `make_structured(model, schema)` → `with_structured_output(schema, method="json_schema", include_raw=True)`. Confirmed against langchain_anthropic 1.5.x source: `method="json_schema"` binds `output_config={"format": ...}` on the request (`ChatAnthropic.with_structured_output`, `elif method == "json_schema":` branch) — no need to drop to `anthropic.Anthropic().messages.parse()`.
- [x] Nodes — retry loop only when `stop_reason in ("max_tokens", "refusal")`; drop parse-retry. Centralized in `llm.invoke_structured` (was duplicated ad-hoc in all 7 call sites); every node (`diagnose`, `rewrite`, `review`, `recruiter_sim`, `hiring_manager`, `extract_requirements`, `classify_role`) now goes through it.
- [x] `rewrite.py`, `diagnose.py` — profile-block breakpoint `{"type":"ephemeral","ttl":"1h"}`; kept 5m on per-job blocks (rewrite's `<section_order>` breakpoint).
- [x] `costs.py` — price 1h writes at 2.0× (`CACHE_WRITE_MULTIPLIER_1H`, 5m stays 1.25×); warn (structlog `cache_miss_unexpected`) when a call with `cache_control` reports `cache_read == 0 and cache_creation == 0`. `llm.invoke_structured` detects `cache_control_expected` by inspecting the actual message blocks, so the warning only fires for calls that really set it.
- [x] `section_order.classify_role` — now returns `(role, usage)`; a cache hit (jd_text already classified) returns `usage=[]` since no call was made, so a retried/cached classification never double- or under-counts cost. `rewrite()` folds this into its own `llm_calls`.
      → verified: `tests/test_llm.py` (retry only on max_tokens/refusal, no retry on other parse failures, cache-miss warning fires only when `cache_control` was actually set), `tests/test_costs.py` (1h write priced at 2.0×, warning gated on `cache_control_expected`). 172/172 tests passing.
      → not verified live (needs a real 20-min-apart run against the Anthropic API, outside this session's scope): `cache_read > 0` on a second run.

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
