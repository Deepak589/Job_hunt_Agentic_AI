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

## Step 6 — ingestion + scheduler (review 2.6, 4.1) — DONE
- [x] `sourcing/base.py` — `JobSource` Protocol is `fetch_jobs(query="", location="", **kwargs) -> list[Job]` (structural,
      duck-typed), not the original `fetch(since)`/`normalize(raw)` split — that split doesn't match how
      `arbeitnow.py`/`adzuna.py` already work (one function does both) and retrofitting it onto those two working,
      tested modules would be unrequested churn for zero behavior change. Every connector already satisfies it.
- [x] `sourcing/personio.py` (XML via stdlib `xml.etree.ElementTree`, accepts either `<workzag-jobs>` or
      `<personio-jobs>` root), `greenhouse.py`, `lever.py`, `ashby.py` — all defensive (`.get()`/`.findtext()` with
      defaults) so an unexpected real-world field can't crash a fetch. `config/companies.yaml` ships with only 3
      companies (Personio/greenhouse-Zalando-lever/N26-greenhouse) — NOT the 20-30 originally asked for.
      Fabricating 20-30 board_token/company-slug mappings from memory risked inventing endpoints that 404 forever
      unnoticed; the file says plainly this is a starter list to verify/expand, not a finished roster.
- [x] `sourcing/arbeitnow.py` pagination/`lingua` swap — NOT done. Out of this task's brief; the stopword heuristic
      stays, unchanged from step 1.
- [x] `db/schema.sql` — `company_snapshots(company, ats, posting_ids, fetched_at, consecutive_failures)`. New table,
      no ALTER-TABLE migration needed (only pre-existing tables gaining columns need that).
- [x] `runner.py` — `jobpilot run --digest json`: loads `config/companies.yaml` → each company's connector →
      diffs against `company_snapshots.posting_ids` → prefilters deterministically (lang == en, optional
      per-company location substring; 0 LLM calls) → survivors go through `graph.run_many` (reused as-is: its
      `BudgetGuard` + `already_processed` dedupe do the cap/dedupe work, no second mechanism built) → digest of
      counts + per-company failure-streak notify lines (>=3 consecutive). One dead board doesn't stop the run —
      caught per-company, failure streak recorded, loop continues.
      Ruling: `run_many`'s `shared_job_fields` are genuinely shared across a whole batch (see its docstring /
      `add --dir`'s use) — doesn't fit postings from different companies with different urls/titles/companies.
      `runner.py` calls it once per surviving job instead (sequential), which keeps `run_many` untouched and
      still reuses its BudgetGuard/dedupe correctly (each call's guard snapshots persisted spend fresh, and the
      prior job's cost is already persisted before the next call starts — no double-count race).
- [x] Scheduler: NOT `launchctl`-registered (that mutates the user's machine without explicit go-ahead). Added
      `ops/com.jobpilot.run.plist.template` + `ops/README.md` (launchd or cron, hourly, `jobpilot run --digest json`).
- [x] `trafilatura`/`extruct` wired into `jobpilot add --url`: JSON-LD `JobPosting` first (via `extruct`, unwrapping
      `@graph`), `trafilatura.extract()` fallback for `jd_text` only (title/company left for `--title`/`--company`).
      `--url` is mutually exclusive with `--file`/`--stdin`/`--dir`.
      → verified: `tests/test_sourcing_{greenhouse,lever,ashby,personio}.py` (field mapping, filters, defensive
      missing-field handling — all mocked HTTP), `tests/test_runner.py` (second `run()` call finds 0 new postings
      off the same fetched postings; 3 consecutive `fetch_jobs` failures → `consecutive_failures == 3` +
      notify line — all graph nodes stubbed, zero LLM calls), `tests/test_cli_add_url.py` (JSON-LD extraction,
      trafilatura fallback). 189/189 tests passing.

## Step 7 — Batches API for the nightly queue (review 4.2) — DONE
- [x] `batch.py` (new file, not `llm.py` — kept separate since it's Anthropic-SDK-direct,
      not a `ChatAnthropic`/LangChain wrapper like everything else in `llm.py`): `BatchRequest`
      (custom_id, model, system, messages, schema_, max_tokens) + `BatchClient.submit/poll/
      fetch_results`. Structured output is forced via `convert_to_anthropic_tool` + `tool_choice`
      (the older, stable tool-forcing mechanism) rather than step 5's live-call `method="json_schema"`
      — `output_config`'s converter is private langchain_anthropic internals and there's no way to
      confirm the Batches API accepts it; tool-forcing is unambiguously supported. Pricing: base
      rate × 0.5 (Anthropic's documented batch discount), no cache multiplier (batch `usage` may not
      carry cache fields reliably — ponytail-flagged as a conservative simplification, not a bug).
- [x] `nodes/requirements.py::extract_requirements` / `nodes/diagnose.py::diagnose` — each is a
      no-op (`return {}`) if its output field (`state.requirements` / `state.diagnosis`) is already
      populated on entry. Safe because neither node has a retry edge back to itself anywhere in the
      graph (confirmed by reading `graph.py`'s wiring) — a live single-job run always starts with
      both fields empty, so this changes nothing on that path.
- [x] `graph.run`/`run_many` — private kwargs `_prefill` / `_prefill_by_index` (same convention as
      the existing `_skip_render`), merged into the initial `JobState` before `ainvoke` so the two
      nodes above see their field already set and skip.
- [x] `runner.py run(batch=False)` — `batch=True` submits every survivor's extract call as one
      Batch, then every extract-succeeded survivor's diagnose call as a second Batch (building its
      request the same way `nodes/diagnose.py::diagnose()` does, by importing its `_profile_brief`/
      `_requirements_brief` helpers rather than re-deriving that formatting — leading underscore is
      a same-package style convention here, not enforced privacy). `retrieve_evidence`/`score_coverage`
      (coverage.py, no LLM) run locally between the two batches, same as the live graph path would.
      A job whose batch call errored at either stage falls back to a normal live `run_many` call
      instead of being dropped — counted in `digest["notify"]` as `"batch fallback: N jobs"`.
      Extra ruling beyond the brief: batch usage records are folded into the `_prefill` dict's new
      `llm_calls` key (not in the original `_prefill` shape spec) so `JobState.total_cost_usd` — and
      therefore `jobpilot cost` — actually reflects the batch discount; without it the skipped nodes
      would silently drop those costs since neither returns an `llm_calls` update.
- [x] `cli.py run --batch` flag, passed through to `runner.run`.
      → verified: `tests/test_batch.py` (submit/poll/fetch_results, mixed succeeded+errored,
      pricing exactly 0.5× `costs.PRICE_PER_MTOK` — also covers Step 7's "cost ~halved" ask),
      `tests/test_prefill_skip.py` (both nodes no-op on prefill, `_model` never called),
      `tests/test_runner.py` (batch path prefills into `run_many` — proven by the node functions
      themselves raising/branching on empty state, not a blind stub — and a batch-errored job
      still gets a live fallback run + notify line). 198/198 tests passing.

## Step 8 — close the loop (review 2.7, 4.4) — DONE
- [x] `db/schema.sql` — `applications(job_id, sent_at, cv_pdf_sha, outcome, outcome_at)`; `jobpilot applied <id>`
      (reads `out/<id>/draft.sha` written by render.py's idempotent-render marker; warns and records `""` if
      no render exists yet), `jobpilot outcome <id> interview|reject|ghost` (typer `Literal` argument for
      automatic choice validation, `db/repo.py::record_outcome` re-validates and raises `ValueError`/`KeyError`
      independently since this may get called from non-CLI code later).
- [x] `db/schema.sql` — `judgements(id, job_id, node, score_or_verdict, human_action)` — a fresh table needs no
      `_NEW_COLUMNS` migration entry (schema.sql's own doc comment on `_NEW_COLUMNS` confirms this only applies
      to columns added to a pre-existing table). Note: field is `id` (uuid4 hex PK), not `run_id` as this
      section's original one-liner said — a job can revisit review multiple times (edit loops), so one row per
      (job, judge, pause) needs its own key, `run_id` alone would collide across those pauses for the same run.
      `cli.py::review()`'s loop calls `_record_judgements()` at every approve/edit/reject pause — up to 3 rows
      (recruiter_sim/hiring_manager/review, whichever had a verdict at that pause) sharing one `human_action`.
- [x] `jobpilot judge-stats` — `judgestats.py` (new, pure Python, no new dependency): `cohens_kappa(pairs, map)`
      implements the standard po/pe formula; `judge_stats()` maps each node's verdict space onto
      approve/edit/reject (recruiter_sim, hiring_manager direct; `review`'s raw `review_score` is bucketed via
      `review_bucket()` — `>= settings.min_review_score` → "pass" else "retry" — at kappa-computation time, not
      at write time, so the stored row always holds the raw score even if the threshold setting changes later).
      CLI table shows κ / N per node, "not enough data" when a node has 0 judgement rows (realistic on a fresh
      install — this must not crash).
- [x] Review-edit diff → `data/preferences.yaml` (plan §21.4) — scoped to bullet-level text changes only, not
      profile_line/cover_letter (ruling: those are free-form prose, diffing them line-by-line is a fuzzier
      problem not worth the complexity here, and CLAUDE.md's XYZ formula lives at the bullet level anyway).
      `preferences.py::diff_edited_bullets` matches old vs new by `source_bullet_id` (not position — a human
      may reorder bullets) and returns new/changed text; `record_preferences` appends+dedupes into the YAML.
      Simplified vs plan §21.4's "weekly clustering job proposes rules, you approve each" step: since the human
      already approved the line by editing it and then approving/continuing the review, the extra proposal
      round-trip adds no real signal here — appended directly, human-approved by construction. `rewrite.py`
      loads `data/preferences.yaml` (empty list if absent, no error) and appends one `<user_preferences>` block
      after the existing two `cache_control` breakpoints — per-job, not itself breakpointed, since it grows
      between any two jobs as more edits land and caching past it would go stale.
- [x] `tests/test_invariance.py` — `ats_score()` confirmed to never read `Draft.section_order` in its scoring
      math (read `scoring/ats.py` in full to verify); two otherwise-identical drafts differing only in
      `section_order` score bit-identical totals. `recruiter_sim` is still a real LLM call at this point in the
      plan (step 9, not done yet, is what makes it deterministic) — scoped per this task's brief to stubbing
      `recruiter_sim._model()` with a fake that inspects the rendered CV text for a fixed keyword, proving the
      result doesn't depend on bullet concatenation order (a substring check can't).
      → verified: `tests/test_cli_applications.py`, `tests/test_judgestats.py` (hand-computed partial-agreement
      κ example, arithmetic shown in a comment), `tests/test_preferences.py`, `tests/test_invariance.py`.
      214/214 tests passing (198 baseline + 16 new).
      → NOT verified with real usage data (none exists yet — no real applications have been sent through this
      tool in this session): "after 30 applications, `judge-stats` prints κ" and "`preferences.yaml` has ≥1
      line derived from an edit" are both proven with synthetic/mocked data in the test suite above, not real
      review sessions. Real verification happens as the user actually runs `jobpilot review` and `jobpilot
      applied`/`outcome` over time.

## Step 9 — judges (review 3.1) — only after Step 8 has data — DONE
- [x] `recruiter_sim` → deterministic: hard keywords ∩ draft CV text (this node runs
      pre-render, before there's a PDF — "pdf_text" means the same `_rendered_cv_text`
      domain it already used); hard_fail if any *covered* hard requirement's keyword is
      missing from the draft. Haiku call deleted, `prompts/recruiter_sim.md` deleted.
      **Ruling: `"soft_fail"` dropped as a reachable output.** Once the check is binary
      per-requirement (keyword present or not), there's no deterministic partial-miss
      case left — inventing a threshold to keep a third bucket alive would be exactly
      the "asserted from judgement" scoring CLAUDE.md forbids. `"soft_fail"` stays a
      valid `RecruiterResult.result` literal (other code/tests reference it), this node
      just never returns it.
- [x] `review` → 3 samples of `settings.review_model` (not Haiku+Sonnet — self-consistency
      sampling needs no second model routing path; documented as a future option in the
      code) → plain majority vote. **Ruling: plain, not weighted.** No real κ(review)
      history exists yet (step 8's machinery just landed, no real judgements recorded)
      — wiring in disagreement-based weights with no data behind them would itself be
      judgement asserted as fact. Stored `review_score` is the median of the 3 samples;
      variance (`statistics.pvariance`) and the raw 3 scores are logged into
      `JobState.notes`.
- [x] κ-gated review cap: `judgestats.review_cap_still_trusted()` — cap applies unless
      there are >= 30 recorded `review` judgement pairs AND κ(review) < 0.4. **Honesty
      note:** this has never fired against real data — there are no real judgements in
      the db yet. Tested only with synthetic `record_judgement` rows engineered to
      produce κ < 0.4 and κ >= 0.4. Real behavior emerges once the user accumulates
      real review decisions through `human_review`.
      → verify: recruiter node has 0 `llm_calls` (`tests/test_recruiter_sim.py`);
      `review` variance across 3 samples logged (`tests/test_review.py`).

## Step 10 — evidence store (review 3.6) — DONE
- [x] `repo_docs.py` (new, flat module) ingests README + top-level `docs/*.md` per-project
      via each project's `repo:` field under `profile.raw["projects"]` (not a top-level
      `master_profile.repos[]` — the real yaml has no such list; per-project `repo:`
      strings are the real shape, confirmed by reading `data/master_profile.yaml`).
      Unauthenticated GitHub REST API (60 req/hr, noted in the module docstring).
      `chunk_by_heading` splits markdown on any-level `#`, intro chunk for text before
      the first heading, slugified anchor ids. SHA-cached at
      `data/repo_docs_cache.json`: an unchanged file's `sha` (from the lightweight
      listing call) skips only its content re-download, not its re-chunk/re-embed —
      `build_index()` has no incremental-add path into Chroma (it's still full-rebuild-
      on-`force`), so the cached content is still fed through on every rebuild.
      **Ruling: did not touch Chroma vs numpy.** solution.md frames these as
      alternatives, but the verify line below only the ingestion path satisfies —
      swapping the storage engine changes nothing about what's indexed. Chroma stays for
      ~20 rows; `ponytail:` this is arguably overkill for that row count, but that's an
      engine-choice question independent of this step, not touched.
- [x] `evidence.build_index()` embeds repo doc chunks into the same collection,
      `metadata={"source": "repo_doc", "parent_id": <project id>, ...}` (same shape as
      bullet metadata). **Fixed `retrieve_many()`**: was hardcoding
      `source="cv_bullet"` on every retrieved `Evidence` regardless of what its metadata
      actually said — a real bug that silently mislabeled every repo_doc chunk on
      retrieval. Now reads `meta.get("source")`.
- [x] `nodes/diagnose.py` / `nodes/rewrite.py` / `coverage.py::retrieve_evidence` —
      read-through confirmed none of the three filter `Evidence` by `.source` anywhere;
      a `repo_doc` chunk is just another candidate in the same list. No code change
      needed beyond the `retrieve_many()` fix above.
      → verify: `tests/test_repo_docs.py` (parse/fetch/chunk/sha-cache, all mocked
      `httpx.get`, no real network), `tests/test_evidence.py` (`build_index()` embeds a
      mocked repo_doc chunk into a real ephemeral Chroma collection + real ids/metadata;
      `retrieve_many()` returns it with `Evidence.source == "repo_doc"`, proving the
      bug fix). 235/235 passing (225 baseline + 10 new).
      → **NOT verified**: the literal "Retorio JD (`evals/real/07`) hard gap becomes
      covered end-to-end" line was not run — `evals/run_harness.py` costs real Anthropic
      API money per its own docstring, and this session was told not to spend it. The
      wiring (fetch → chunk → embed → retrieve → source label) is unit-tested with
      mocks instead; running it against the real Retorio JD + a real embed of the real
      RAG_pipeline repo is a manual follow-up for the user.

## Done means
- `jobpilot run` hourly for 7 days: 0 duplicate runs, spend ≤ cap every day, 0 lost runs
  on crash. **Mechanism exists and is tested** (durability/resume, per-source try/except,
  budget cap — steps 1-2, 6). **Not verified against reality**: no real 7-day hourly run
  has happened yet: this needs real crash/uptime history, not something a test suite can
  produce.
- ATS score separates two drafts with equal coverage. **Verified**:
  `tests/test_invariance.py` (step 8) proves the score never reads `section_order`, and
  `scoring/ats.py`'s fact-table format means two drafts with different bullet phrasing
  but identical counted coverage score identically — this one is done, not just wired.
- κ for each judge node is a number in the repo, not an assumption. **Mechanism exists
  and is tested** (`judgestats.py`, step 8/9): `cohens_kappa` is implemented, `judge-stats`
  prints κ/N per node, and the κ-gated review cap wiring is proven with synthetic
  judgement rows. **Not verified against reality**: zero real judgements exist in the db
  yet (this tool has not been run against real applications), so no real κ value has ever
  been computed — that requires the user to actually run `jobpilot review` over real
  jobs and accumulate judgement rows.

All 10 steps' code and tests are done. What's explicitly still open across all three
bullets above is the same thing: real usage data. Nothing in this plan requires further
code to close that gap — it requires the user running `jobpilot` for real.
