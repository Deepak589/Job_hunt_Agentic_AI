# Graph Report - Agentic_ai  (2026-09-29)

## Corpus Check
- 107 files · ~102,059 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1352 nodes · 2600 edges · 94 communities (85 shown, 9 thin omitted)
- Extraction: 90% EXTRACTED · 10% INFERRED · 0% AMBIGUOUS · INFERRED: 267 edges (avg confidence: 0.6)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `0f1cc901`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 45|Community 45]]
- [[_COMMUNITY_Community 46|Community 46]]
- [[_COMMUNITY_Community 47|Community 47]]
- [[_COMMUNITY_Community 48|Community 48]]
- [[_COMMUNITY_Community 49|Community 49]]
- [[_COMMUNITY_Community 50|Community 50]]
- [[_COMMUNITY_Community 51|Community 51]]
- [[_COMMUNITY_Community 52|Community 52]]
- [[_COMMUNITY_Community 53|Community 53]]
- [[_COMMUNITY_Community 54|Community 54]]
- [[_COMMUNITY_Community 55|Community 55]]
- [[_COMMUNITY_Community 56|Community 56]]
- [[_COMMUNITY_Community 57|Community 57]]
- [[_COMMUNITY_Community 58|Community 58]]
- [[_COMMUNITY_Community 59|Community 59]]
- [[_COMMUNITY_Community 60|Community 60]]
- [[_COMMUNITY_Community 61|Community 61]]
- [[_COMMUNITY_Community 62|Community 62]]
- [[_COMMUNITY_Community 63|Community 63]]
- [[_COMMUNITY_Community 64|Community 64]]
- [[_COMMUNITY_Community 65|Community 65]]
- [[_COMMUNITY_Community 66|Community 66]]
- [[_COMMUNITY_Community 67|Community 67]]
- [[_COMMUNITY_Community 68|Community 68]]
- [[_COMMUNITY_Community 69|Community 69]]
- [[_COMMUNITY_Community 70|Community 70]]
- [[_COMMUNITY_Community 71|Community 71]]
- [[_COMMUNITY_Community 72|Community 72]]
- [[_COMMUNITY_Community 73|Community 73]]
- [[_COMMUNITY_Community 74|Community 74]]
- [[_COMMUNITY_Community 75|Community 75]]
- [[_COMMUNITY_Community 76|Community 76]]
- [[_COMMUNITY_Community 77|Community 77]]
- [[_COMMUNITY_Community 78|Community 78]]
- [[_COMMUNITY_Community 79|Community 79]]
- [[_COMMUNITY_Community 80|Community 80]]
- [[_COMMUNITY_Community 81|Community 81]]
- [[_COMMUNITY_Community 82|Community 82]]
- [[_COMMUNITY_Community 83|Community 83]]
- [[_COMMUNITY_Community 84|Community 84]]
- [[_COMMUNITY_Community 85|Community 85]]
- [[_COMMUNITY_Community 86|Community 86]]
- [[_COMMUNITY_Community 87|Community 87]]
- [[_COMMUNITY_Community 88|Community 88]]
- [[_COMMUNITY_Community 89|Community 89]]

## God Nodes (most connected - your core abstractions)
1. `JobState` - 53 edges
2. `Job` - 51 edges
3. `Profile` - 47 edges
4. `scored()` - 37 edges
5. `BaseModel` - 35 edges
6. `req()` - 34 edges
7. `Draft` - 33 edges
8. `JobPilot — Multi-Agent Job Search & CV Tailoring System` - 27 edges
9. `make_structured()` - 25 edges
10. `invoke_structured()` - 25 edges

## Surprising Connections (you probably didn't know these)
- `Profile` --uses--> `Profile`  [INFERRED]
  scripts/build_cv_data.py → src/agentic_ai/profile.py
- `test_judge_stats_from_recorded_rows()` --calls--> `record_judgement()`  [INFERRED]
  tests/test_judgestats.py → src/agentic_ai/db/repo.py
- `MonkeyPatch` --uses--> `MissingCredentialsError`  [INFERRED]
  tests/test_sourcing_adzuna.py → src/agentic_ai/sourcing/adzuna.py
- `_insert_job()` --calls--> `init_db()`  [INFERRED]
  tests/test_cli_applications.py → src/agentic_ai/db/repo.py
- `test_check_budget_blocks_at_cap()` --calls--> `persist_run()`  [INFERRED]
  tests/test_cli_cost.py → src/agentic_ai/db/repo.py

## Import Cycles
- 1-file cycle: `src/agentic_ai/budget.py -> src/agentic_ai/budget.py`
- 1-file cycle: `src/agentic_ai/db/repo.py -> src/agentic_ai/db/repo.py`

## Communities (94 total, 9 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.06
Nodes (73): gate_reason(), hard_gap_gate(), log_skip(), The full requirement, plus each clause inside it.      An embedding of four idea, Per requirement, top-k bullets from the evidence store.      Each requirement is, Why this job is a skip, or None to proceed. Pure — the routing fn wraps it., Conditional edge: 'skip' or 'proceed'., retrieve_evidence() (+65 more)

### Community 1 - "Community 1"
Cohesion: 0.07
Nodes (56): _blocked_language(), _eligibility_terms(), _is_onsite(), _is_unrecorded_fact(), _keyword_hit(), keyword_set(), _location_reachable(), location_terms() (+48 more)

### Community 2 - "Community 2"
Cohesion: 0.07
Nodes (47): _load_companies(), _new_digest(), Batch-mode processing for every survivor (solution.md step 7)., Poll every company in `companies_path` (default `config/companies.yaml`), run, One survivor through the full pipeline via `run_many`, persisted, folded into, run(), _run_batch(), _run_one_live() (+39 more)

### Community 3 - "Community 3"
Cohesion: 0.09
Nodes (49): AtsScore, plan.md §6. Counts first, arithmetic second — a score with no fact table under i, AtsScore, AtsVerdict, ats_score(), _frac(), gates_failed(), parse_pdf() (+41 more)

### Community 4 - "Community 4"
Cohesion: 0.06
Nodes (37): Settings — thresholds, model ids, paths. Override any field via .env or environm, Settings, Scores, BaseSettings, _model(), _prompt(), review — LLM call 4, Sonnet (plan.md §3, §7.4). Loops to rewrite, max 2 attempts, 3-sample ensemble, plain majority vote (solution.md step 9).      Ruling: 3 inde (+29 more)

### Community 5 - "Community 5"
Cohesion: 0.07
Nodes (45): index_build(), index_calibrate(), (Re)build the evidence store from master_profile.yaml., Measure SEM_THRESHOLD against the hand-labeled probes., build_index(), calibrate(), _client(), embed() (+37 more)

### Community 6 - "Community 6"
Cohesion: 0.09
Nodes (39): build_profile_dict(), _bullet_dict(), _duration_years(), _education_model(), EducationList, _experience_model(), ExperienceList, extract() (+31 more)

### Community 7 - "Community 7"
Cohesion: 0.12
Nodes (25): BatchClient, BatchRequest, _node_from_custom_id(), BatchClient — Anthropic Batches API for the nightly queue (solution.md step 7)., Anthropic's Batches API is documented at 50% of the standard per-token rate, Thin wrapper over `anthropic.Anthropic().messages.batches`. Anthropic-only by, Blocks until `processing_status == "ended"`. Raises `TimeoutError` past, `{custom_id: {"parsed": <schema instance|None>, "usage": <dict|None>, "error": < (+17 more)

### Community 8 - "Community 8"
Cohesion: 0.10
Nodes (32): add(), _check_budget(), _edit_draft(), _log_event(), _log_run(), outcome(), _print_jobs_table(), profile_check() (+24 more)

### Community 9 - "Community 9"
Cohesion: 0.06
Nodes (33): Deepak589/cloudnotes/README.md, content, sha, Deepak589/Job_hunt_Agentic_AI/docs/solution.md, content, sha, Deepak589/Job_hunt_Agentic_AI/docs/system_design_review_2026-09-18.md, content (+25 more)

### Community 10 - "Community 10"
Cohesion: 0.10
Nodes (24): expand_metric_tokens(), The hand-listed `allowed_metrics`. A cached render of the bullets, not truth., Every numeric token a generated bullet may legitimately print.          Derived, Drift between the bullets and the cached `allowed_metrics` list.          Return, Every numeric surface form a set of declared metrics legitimises.      Metric st, profile(), Profile loader, metric expansion, and yaml integrity.  The metric tests are the, A typo in a skill's evidence list silently breaks retrieval — it must error. (+16 more)

### Community 11 - "Community 11"
Cohesion: 0.12
Nodes (24): Output of the `recruiter_sim` node — role 4 (CLAUDE.md). Fast, shallow,     keyw, RecruiterResult, hard_fail' skips before render — CLAUDE.md role 4: "Hard-fail = don't recommend, recruiter_gate(), _fake_recruiter_sim(), _patch_llm_nodes(), JobState, Phase 3 — recruiter_gate routing (pure) + the checkpointer/interrupt/resume mech (+16 more)

### Community 12 - "Community 12"
Cohesion: 0.15
Nodes (26): estimated_job_cost(), Rolling average cost of a past run, as the estimate for one not-yet-run job., build_graph(), _build_job(), _get_paused_state(), human_review(), human_review_gate(), load_job() (+18 more)

### Community 13 - "Community 13"
Cohesion: 0.13
Nodes (23): _has_cache_control(), invoke_structured(), make_llm(), Shared ChatAnthropic factory. langchain_anthropic's ChatAnthropic already forwar, True if any message block in this call actually set `cache_control` — messages, Call a `make_structured` runnable, recording usage for every call made (a retrie, Any, ChatAnthropic (+15 more)

### Community 14 - "Community 14"
Cohesion: 0.07
Nodes (26): 1. What exists (verified in code), 2.1 Duplicate spend — no "seen" tracking, 2.2 Budget cap is per-invocation, not per-job, 2.3 Crash = lost money, 2.4 Sync nodes under `ainvoke`, 2.5 Reranker runs on every query, result discarded, 2.6 Ingestion is page-1-only, no scheduling, 2.7 Learning loop is open (+18 more)

### Community 15 - "Community 15"
Cohesion: 0.08
Nodes (24): Bugs found and fixed while building, Carried into Phase 3 (per plan.md §16), Dead-state cleanup + retry-cache — fixed 2026-09-16, Done means, Fixed in the final whole-branch review pass, Known items carried forward (real findings, not hypothetical), Open, carried into Phase 2, Phase 1 — core loop, manual JD input (+16 more)

### Community 16 - "Community 16"
Cohesion: 0.15
Nodes (23): applied(), Record that job_id's tailored CV was sent. Reads the rendered draft's hash from, already_processed(), get_company_snapshot(), get_job(), init_db(), judgement_pairs(), jobs/runs persistence — the cost ledger (plan.md §11-12, Phase 3).  One row per (+15 more)

### Community 17 - "Community 17"
Cohesion: 0.16
Nodes (17): jobpilot run — poll every configured company, diff against last-seen postings, p, Job, Typed state threaded through tailor_graph (plan.md §2).  Phase 1 fields only. `D, _employment_type(), fetch_jobs(), Ashby Job Board API ingestion — public, no auth (solution.md step 6).  Shape per, Shared sourcing contract + small helpers reused across connectors (solution.md s, Same pattern as arbeitnow.py's `_strip_html` — shared here since 4 new     conne (+9 more)

### Community 18 - "Community 18"
Cohesion: 0.15
Nodes (20): diff_edited_bullets(), load_preferences(), Procedural memory from review edits (plan.md §21.4, solution.md step 8) — scoped, New/changed bullet texts in `new` vs `old`, matched by source_bullet_id (a human, Append+dedupe into data/preferences.yaml. Creates the file if absent., Empty list if the file doesn't exist — a fresh install has none yet., record_preferences(), Draft (+12 more)

### Community 19 - "Community 19"
Cohesion: 0.14
Nodes (20): classify_role(), _model(), _prompt(), Role classification + deterministic section order (plan.md §7, CLAUDE.md Section, classify_role — which of the four CLAUDE.md role archetypes this JD is., Cached by jd_text — a review/fact-validation retry re-invokes rewrite for the, RoleClassification, section_order_for() (+12 more)

### Community 20 - "Community 20"
Cohesion: 0.13
Nodes (19): HiringManagerVerdict, Output of the `hiring_manager` node — role 5 (CLAUDE.md). The "could you defend, ExtractedRequirement, One requirement as stated in the JD.      Deliberately narrower than `state.Requ, emit_requirements — every requirement stated in the job description., RequirementList, _fake_hiring_manager(), _batch_job() (+11 more)

### Community 21 - "Community 21"
Cohesion: 0.19
Nodes (17): _rate(), Token usage + $ cost per LLM call (improvement.md #1 — was unimplemented).  Anth, One usage record from a LangChain AIMessage's `.usage_metadata`.      LangChain', record_usage(), AIMessage, _msg(), AIMessage, improvement.md #1 — token usage + $ cost per LLM call. (+9 more)

### Community 22 - "Community 22"
Cohesion: 0.19
Nodes (16): cost(), Spend summary from the runs ledger — per day and all-time., cost_summary(), persist_run(), Per-day + all-time run count/tokens/$ from the `runs` table.      `since`, if gi, Upsert the job row, insert one run row. Returns the new run_id., datetime, JobState (+8 more)

### Community 23 - "Community 23"
Cohesion: 0.21
Nodes (16): build_cover_letter_render_data(), build_cv_render_data(), _draft_hash(), _fmt_month(), job_dir_name(), render_documents — Typst PDF rendering (plan.md §3, §10).  Two data-building fun, Human-readable output folder name — company-title-<first 8 hex of job.id>., 2025-09' -> 'Sep 2025'; None/empty -> 'Present' (open-ended education/roles). (+8 more)

### Community 24 - "Community 24"
Cohesion: 0.12
Nodes (16): 0. Design principles, 10. Document rendering, 11. Persistence, 13. Reliability, 14. Evaluation, 15. Repo layout, 16. Build order, 17. Prefilter rules (from CLAUDE.md priority rules) (+8 more)

### Community 25 - "Community 25"
Cohesion: 0.18
Nodes (13): _fake_diagnose(), _isolate_db(), _patch_llm_nodes(), graph.run_many — concurrent multi-JD execution (§ jobpilot add --dir). Every LLM, solution.md step 1: the cap check reads persisted + in-flight-RESERVED spend, so, run_many now checks already_processed()/BudgetGuard against settings.jobs_db_pat, All 6 LLM-calling nodes route through ChatAnthropic; monkeypatching the 4 that, solution.md step 1: re-running the same JD text (same content-hash job id)     m (+5 more)

### Community 26 - "Community 26"
Cohesion: 0.18
Nodes (14): job_id(), Identifies the POSTING, not the text (solution.md step 1): same source+url fetch, _employment_type(), fetch_jobs(), MissingCredentialsError, Adzuna job-board ingestion — REST API, requires ADZUNA_APP_ID/ADZUNA_APP_KEY.  N, ADZUNA_APP_ID / ADZUNA_APP_KEY not set., _employment_type() (+6 more)

### Community 27 - "Community 27"
Cohesion: 0.24
Nodes (14): log_recruiter_fail(), recruiter_sim — deterministic keyword-literal ATS scan (plan.md §7, CLAUDE.md ro, Deterministic literal-keyword screen. Only checks requirements the pipeline, A draft the recruiter screen would hard-fail must not reach render_documents., recruiter_sim(), _rendered_cv_text(), JobState, Requirement (+6 more)

### Community 28 - "Community 28"
Cohesion: 0.13
Nodes (14): File Structure, Global Constraints, Phase 2 — Tailoring + the Guardrail — Implementation Plan, Self-Review Notes, Task 10: CLI — print the diagnosis, draft, and ATS report; wire `--no-render`, Task 1: State schema — Diagnosis, Draft, DraftBullet, Task 2: `validators/facts.py` — the fact validator, test-first, Task 3: `section_order.py` — role classifier + deterministic section order (+6 more)

### Community 29 - "Community 29"
Cohesion: 0.15
Nodes (3): _patch_llm_nodes(), evals/run_harness.py — proves the harness's own collect/diff logic works, with e, test_collect_metrics_runs_fixture_through_stubbed_graph()

### Community 30 - "Community 30"
Cohesion: 0.15
Nodes (12): Done means, Phase 4 — Real-time hardening (solution to docs/system_design_review_2026-09-18.md), Step 10 — evidence store (review 3.6) — DONE, Step 1 — stop money leaks (review 2.1, 2.2) — DONE, Step 2 — durability (review 2.3) — DONE, Step 3 — hot-path waste (review 2.4, 2.5) — DONE, Step 4 — ATS score means something (review 3.4), Step 5 — LLM plumbing (review 3.2, 3.3) — DONE (+4 more)

### Community 31 - "Community 31"
Cohesion: 0.15
Nodes (3): Exception, Mocked HTTP layer — no real network calls, no real GitHub API hits (same pattern, _resp()

### Community 32 - "Community 32"
Cohesion: 0.15
Nodes (12): 1. Zero-shot retrieval (the results above), 2. Trained dual-encoder (`src/`), Data, Food Image-to-Recipe Retrieval, Further reading, Fusion weight ablation, Layout, Results (+4 more)

### Community 33 - "Community 33"
Cohesion: 0.22
Nodes (7): _companies_yaml(), _isolate_db(), _patch_llm_nodes(), runner.py — poll -> snapshot diff -> prefilter -> run_many -> digest (solution.m, Same pattern as tests/test_run_many.py's `_isolate_db` — point the shared     `s, test_run_twice_produces_zero_new_postings_second_time(), test_three_consecutive_failures_recorded_and_notified()

### Community 34 - "Community 34"
Cohesion: 0.23
Nodes (8): BudgetExceeded, check_budget(), Daily spend cap (solution.md step 1). Two call sites:  - `check_budget()` — a pl, Today's persisted spend has already hit (or would exceed) the daily cap., Raise BudgetExceeded if today's persisted spend already hit the cap.     No-op w, spent_today(), _today_start(), datetime

### Community 35 - "Community 35"
Cohesion: 0.23
Nodes (11): judge_stats_cmd(), Cohen's kappa per judge node vs what the human actually did at review., cohens_kappa(), judge_stats(), Cohen's kappa per judge node vs human action (solution.md step 8). Pure Python —, review_score >= settings.min_review_score -> 'pass' else 'retry' (the judge-outp, po/pe/kappa over `pairs` = (judge_output, human_action). None if `pairs` is empt, Gate for `scoring/ats.py`'s review cap (solution.md step 9).      The cap holds (+3 more)

### Community 36 - "Community 36"
Cohesion: 0.17
Nodes (4): Profile, Skill names, lowercased. The keyword half of the §6 two-signal match., Stack entries on experience/project records — tech not always in `skills`., Integrity errors in the yaml itself. Empty list = clean.          Catches what n

### Community 37 - "Community 37"
Cohesion: 0.17
Nodes (11): certifications, contact, education, experience, languages, name, photo, profile (+3 more)

### Community 38 - "Community 38"
Cohesion: 0.21
Nodes (8): check_metrics(), check_never_claim(), check_skills(), cv_skill_tokens(), norm(), not_shipped bullets must not surface on the CV., Flatten CV skill strings into atoms, splitting parentheticals too., Every number the CV states must trace to a metric in master_profile.yaml.      E

### Community 39 - "Community 39"
Cohesion: 0.23
Nodes (7): _candidates(), Evidence, Cross-encoder reranking (evidence.rerank), tested in isolation — no network, no, _StubCrossEncoder, test_build_index_tags_repo_doc_chunks_and_retrieve_many_reports_their_source(), test_rerank_can_reorder_by_cross_encoder_score(), test_rerank_sets_rerank_score_without_touching_similarity()

### Community 40 - "Community 40"
Cohesion: 0.22
Nodes (6): Bullet, Loader + validation over data/master_profile.yaml (plan.md §5.1).  master_profil, The text that gets embedded — outcome + metric + method (plan.md §5.2)., Every node in the graph calls this once per job — cache on (path, mtime_ns), Skill, Path

### Community 41 - "Community 41"
Cohesion: 0.18
Nodes (10): Architecture, Commands, Conventions, Current focus, Definition of done, Design decisions — do not undo, JobPilot — Agentic_ai, Models (+2 more)

### Community 42 - "Community 42"
Cohesion: 0.31
Nodes (10): collect_metrics(), diff_all(), diff_metrics(), fixture_paths(), main(), Path, Regression harness for the JobPilot pipeline.  Runs JD fixtures (evals/golden/*., Run one fixture through the graph and pull out the regression-relevant fields. (+2 more)

### Community 43 - "Community 43"
Cohesion: 0.24
Nodes (9): Diagnosis, Output of the `diagnose` node — line-by-line CV vs JD (plan.md §7.1)., _fake_diagnose(), _fake_diagnose(), _fake_diagnose(), _job(), Phase 1 states (a skip, before diagnose ever runs) must still validate., test_diagnosis_and_draft_default_to_none() (+1 more)

### Community 44 - "Community 44"
Cohesion: 0.20
Nodes (9): 1. Cost / token accounting, 2. Latency / concurrency, 3. Persistence / resumability, 4. Guardrail gaps, 5. Five-role pipeline (Phase 3, plan.md §16) — DONE 2026-09-16, 6. Dead / unwired state, 7. Job sourcing, 8. Testing / eval gaps (+1 more)

### Community 45 - "Community 45"
Cohesion: 0.31
Nodes (9): diagnose(), _model(), _profile_brief(), _prompt(), diagnose — LLM call 2, Sonnet (plan.md §3, §7.1). Sees the full profile — unlike, Graph node. No-op if `state.diagnosis` is already populated (solution.md step 7, _requirements_brief(), JobState (+1 more)

### Community 46 - "Community 46"
Cohesion: 0.20
Nodes (9): Eval harness (regression detection for scoring/retrieval changes), JobPilot, Layout, Pipeline, Production fixes, Setup, Testing, Tracing (optional) (+1 more)

### Community 47 - "Community 47"
Cohesion: 0.22
Nodes (6): BudgetGuard, Reserves in-flight spend against the daily cap for one run_many() batch.      `p, Replace a job's reservation with its real cost once it finishes., Drop a reservation for a job that never ran (skipped, rejected)., Draft, Job

### Community 48 - "Community 48"
Cohesion: 0.31
Nodes (8): _fetch_job_posting(), _jobposting_from_jsonld(), Fetch `url` and pull JD fields — JSON-LD JobPosting first (structured, reliable), Find a schema.org JobPosting among extruct's json-ld blocks, unwrapping @graph, _mock_response(), `jobpilot add --url` — JSON-LD JobPosting extraction, trafilatura fallback (solu, test_fetch_job_posting_extracts_jsonld_jobposting(), test_fetch_job_posting_falls_back_to_trafilatura_without_jsonld()

### Community 49 - "Community 49"
Cohesion: 0.31
Nodes (8): DraftBullet, _capped_bullets(), Structural overflow guard: cap bullets/section and chars/bullet so a draft can't, _capped_bullets — structural overflow guard (max bullets/section, max chars/bull, test_extra_bullets_beyond_the_cap_are_dropped(), test_overlong_bullet_is_not_truncated_if_it_would_cut_the_metric(), test_overlong_bullet_without_a_metric_is_truncated(), test_short_bullets_pass_through_unchanged()

### Community 50 - "Community 50"
Cohesion: 0.22
Nodes (9): Appendix A — Dependencies, Core orchestration, Deliberately excluded, Dev, Environment, Ingest (§4), Ops, Retrieval (+1 more)

### Community 51 - "Community 51"
Cohesion: 0.44
Nodes (8): _insert_job(), _isolate_db(), jobpilot applied / jobpilot outcome (solution.md step 8)., test_applied_records_application_with_sha_when_render_exists(), test_applied_warns_when_no_render_found(), test_outcome_rejects_invalid_value(), test_outcome_updates_row_after_applied(), test_outcome_without_prior_applied_is_a_clean_error()

### Community 52 - "Community 52"
Cohesion: 0.36
Nodes (7): hiring_manager(), _model(), _profile_bullets_json(), _prompt(), hiring_manager — LLM call 6, Sonnet (plan.md §7, CLAUDE.md role 5). "Could you d, JobState, Profile

### Community 53 - "Community 53"
Cohesion: 0.25
Nodes (7): Atomization, Core rule, Dates, extract_profile — v1, Output, Profile summary, Skills

### Community 54 - "Community 54"
Cohesion: 0.36
Nodes (7): _detect_lang(), _employment_type(), fetch_jobs(), Arbeitnow job-board ingestion — public API, no auth (plan.md sourcing).  Real re, Fetch postings from Arbeitnow and map them into `Job`.      Arbeitnow's public A, _strip_html(), Job

### Community 55 - "Community 55"
Cohesion: 0.39
Nodes (7): _draft(), A section absent from section_order (e.g. no 'skills' bullets drafted) must not, solution.md step 2: idempotent render — a second call with the same draft must, test_cover_letter_render_data_has_company_and_body(), test_cv_render_data_matches_existing_template_shape(), test_cv_render_data_only_includes_sections_in_section_order(), test_render_documents_skips_compile_when_draft_unchanged()

### Community 56 - "Community 56"
Cohesion: 0.38
Nodes (6): Element, _employment_type(), fetch_jobs(), _jd_text(), Personio public XML job feed ingestion — no auth (solution.md step 6).  Shape pe, Job

### Community 57 - "Community 57"
Cohesion: 0.38
Nodes (6): MonkeyPatch, _mock_response(), Mocked HTTP layer against Adzuna's documented response shape — no real network c, test_fetch_jobs_id_matches_source_and_url(), test_fetch_jobs_maps_fields_with_explicit_credentials(), test_fetch_jobs_missing_credentials_raises_clear_error()

### Community 58 - "Community 58"
Cohesion: 0.29
Nodes (7): 22.1 Components, 22.2 Non-negotiable rules, 22.3 Handoff output, 22.4 Why this is last, 22. Assisted apply (Phase 6 — optional, build last), What this is, What this is not

### Community 59 - "Community 59"
Cohesion: 0.29
Nodes (6): Every bullet MUST cite a real source, If you are given prior validation errors, Output, prompts/rewrite.md — v1, The X-Y-Z formula (mandatory, every bullet), What to do

### Community 60 - "Community 60"
Cohesion: 0.48
Nodes (6): build(), _bullet_sentence(), main(), _org_line(), _project_meta(), Profile

### Community 61 - "Community 61"
Cohesion: 0.43
Nodes (6): _mock_response(), Mocked HTTP layer — no real network calls (real shape curled 2026-09-18)., A job seen via sourcing and a manual paste of the same JD text must share a, test_fetch_jobs_content_hash_matches_manual_content_hash(), test_fetch_jobs_filters_by_query_and_location(), test_fetch_jobs_maps_fields()

### Community 62 - "Community 62"
Cohesion: 0.38
Nodes (6): _mock_response(), Mocked HTTP layer — no real network calls (shape per Greenhouse's documented API, An unexpected/missing field must not crash the whole fetch., test_fetch_jobs_defensive_against_missing_fields(), test_fetch_jobs_filters_by_query_and_location(), test_fetch_jobs_maps_fields()

### Community 63 - "Community 63"
Cohesion: 0.48
Nodes (6): _mock_response(), Mocked HTTP layer — no real network calls (shape per Personio's documented XML f, test_fetch_jobs_accepts_personio_jobs_root_variant(), test_fetch_jobs_defensive_against_missing_fields(), test_fetch_jobs_filters_by_query_and_location(), test_fetch_jobs_maps_fields()

### Community 64 - "Community 64"
Cohesion: 0.33
Nodes (6): 21.1 Five memory types, 21.2 Scoped access — which node sees what, 21.3 Write policy, 21.4 Procedural memory — learning from your edits, 21.5 Context budget per node, 21. Memory architecture

### Community 65 - "Community 65"
Cohesion: 0.47
Nodes (4): _job(), extract_requirements / diagnose skip their LLM call when the field they'd fill i, test_diagnose_skips_when_diagnosis_already_set(), test_extract_requirements_skips_when_requirements_already_set()

### Community 66 - "Community 66"
Cohesion: 0.47
Nodes (4): _mock_response(), Mocked HTTP layer — no real network calls (shape per Ashby's documented API, not, test_fetch_jobs_filters_by_query_and_location(), test_fetch_jobs_maps_fields()

### Community 67 - "Community 67"
Cohesion: 0.47
Nodes (4): _mock_response(), Mocked HTTP layer — no real network calls (shape per Lever's documented API, not, test_fetch_jobs_filters_by_query_and_location(), test_fetch_jobs_maps_fields()

### Community 68 - "Community 68"
Cohesion: 0.40
Nodes (3): _mock_response(), Mocked HTTP layer — no real network/Apify calls (shape per a live dataset pull)., test_fetch_jobs_maps_fields()

### Community 69 - "Community 69"
Cohesion: 0.60
Nodes (5): _extract_pdf_text(), init_profile_from_cv(), Extract `cv_path` into data/master_profile.yaml, regenerate cv_data.json and, write_profile_yaml(), Path

### Community 70 - "Community 70"
Cohesion: 0.40
Nodes (5): 20.1 Scheduled run, 20.2 Review surface, 20.3 Notifications, 20.4 Notes and rollups, 20. Cowork control plane — review, notes, notifications

### Community 71 - "Community 71"
Cohesion: 0.40
Nodes (4): Output, prompts/diagnose.md — v1, Rules, Your job

### Community 72 - "Community 72"
Cohesion: 0.40
Nodes (4): Classify every requirement as exactly one of, extract_requirements — v1, Output, Rules

### Community 73 - "Community 73"
Cohesion: 0.40
Nodes (4): Output, prompts/hiring_manager.md — v1, What you see, Your job

### Community 74 - "Community 74"
Cohesion: 0.50
Nodes (3): Option A — launchd (macOS), Option B — cron, Scheduling `jobpilot run`

### Community 75 - "Community 75"
Cohesion: 0.50
Nodes (4): 19.2 Limits, 19.3 Limit guard, 19. Batch runner — "the conductor", Responsibilities

### Community 76 - "Community 76"
Cohesion: 0.50
Nodes (4): 4.1 Company career-page watcher, 4.2 URL → JD extraction, 4. Data sources, Source interface

### Community 77 - "Community 77"
Cohesion: 0.50
Nodes (4): 5.1 Master profile (single source of truth), 5.2 Evidence chunks, 5.3 GitHub ingestion, 5. Evidence store — the thing that makes this better than prompting

### Community 78 - "Community 78"
Cohesion: 0.50
Nodes (3): Output, prompts/review.md — v1, Score against

### Community 79 - "Community 79"
Cohesion: 0.50
Nodes (3): Protocol, JobSource, Job

### Community 82 - "Community 82"
Cohesion: 0.67
Nodes (3): 6. Scoring — deterministic, per-requirement, ATS score (target ≥ 95, floor 90), The hard-gap gate

## Knowledge Gaps
- **234 isolated node(s):** `PreToolUse`, `build_cv.sh script`, `name`, `tagline`, `photo` (+229 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **9 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Profile` connect `Community 36` to `Community 0`, `Community 1`, `Community 2`, `Community 3`, `Community 4`, `Community 5`, `Community 6`, `Community 39`, `Community 8`, `Community 40`, `Community 10`, `Community 45`, `Community 17`, `Community 19`, `Community 52`, `Community 23`, `Community 60`?**
  _High betweenness centrality (0.065) - this node is a cross-community bridge._
- **Why does `BaseModel` connect `Community 6` to `Community 2`, `Community 3`, `Community 36`, `Community 5`, `Community 4`, `Community 7`, `Community 40`, `Community 43`, `Community 11`, `Community 17`, `Community 19`, `Community 20`, `Community 23`?**
  _High betweenness centrality (0.027) - this node is a cross-community bridge._
- **Why does `Job` connect `Community 17` to `Community 2`, `Community 3`, `Community 4`, `Community 5`, `Community 6`, `Community 11`, `Community 12`, `Community 47`, `Community 18`, `Community 20`, `Community 21`, `Community 54`, `Community 23`, `Community 56`, `Community 22`, `Community 26`, `Community 27`?**
  _High betweenness centrality (0.025) - this node is a cross-community bridge._
- **Are the 28 inferred relationships involving `JobState` (e.g. with `ReviewResult` and `JobState`) actually correct?**
  _`JobState` has 28 INFERRED edges - model-reasoned connections that need verification._
- **Are the 28 inferred relationships involving `Job` (e.g. with `ReviewResult` and `Draft`) actually correct?**
  _`Job` has 28 INFERRED edges - model-reasoned connections that need verification._
- **Are the 20 inferred relationships involving `Profile` (e.g. with `ClientAPI` and `Collection`) actually correct?**
  _`Profile` has 20 INFERRED edges - model-reasoned connections that need verification._
- **What connects `PreToolUse`, `build_cv.sh script`, `name` to the rest of the system?**
  _523 weakly-connected nodes found - possible documentation gaps or missing edges._