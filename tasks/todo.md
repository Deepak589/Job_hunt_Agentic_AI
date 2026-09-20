# Phase 1 — core loop, manual JD input

Plan: `~/.claude/plans/twinkly-wibbling-sun.md` (from plan.md §16 Phase 1)

## Step 0 — preconditions
- [x] P2: master_profile.yaml header says yaml is truth, cv_data.json is a render
      → verify: no file in repo claims the PDF/CV is authoritative
- [x] P4: check_links skips absent optional keys
      → verify: profile_sync.py prints no NOTES section
- [x] P1: profile_sync.py check_metrics exact match, not substring (after Step 2)
      → verify: 9/40/13/0.85/54 rejected; profile_sync.py still exits 0

## Step 1 — state + config
- [x] src/agentic_ai/state.py — plan.md §2, Phase 1 fields only
- [x] src/agentic_ai/config.py — thresholds, model ids, paths
      → verify: JobState round-trips a fixture Job through model_dump_json

## Step 2 — profile loader
- [x] src/agentic_ai/profile.py — load + validate master_profile.yaml
      → verify: tests/test_profile.py asserts 58 skills / 20 bullets / 102 tags;
        validate() clean on real file, 1 error on dangling-evidence fixture

## Step 3 — evidence store
- [x] src/agentic_ai/evidence.py — chroma + bge-m3, 20 bullet chunks, cosine space
- [x] calibrate SEM_THRESHOLD from measured positive/negative pair
      → verify: index build = 20 chunks; "hybrid retrieval BM25" tops proj.rag_pipeline.b1;
        "Rust systems programming" best sim below threshold

## Step 4 — extract_requirements (Haiku)
- [x] prompts/extract_requirements.md + src/agentic_ai/nodes/requirements.py
      → verify: 3 real JDs, hard requirements match hand-labeling; saved to evals/golden/

## Step 5 — coverage + gate (deterministic)
- [x] src/agentic_ai/coverage.py — retrieve_evidence, score_coverage, hard_gap_gate
      → verify: tests/test_coverage.py — Rust/Go JD routes skip; C1-German disqualifier
        routes skip regardless of coverage. No network.

## Step 6 — graph
- [x] src/agentic_ai/graph.py — LangGraph StateGraph, conditional edge on the gate
      → verify: draw_mermaid() shows 6 nodes + both branches; invoke() populates scores

## Step 7 — CLI
- [x] src/agentic_ai/cli.py — add / index build / profile check
      → verify: end to end on a real JD; report is actionable

## Done means
- [x] hard-gap JD exits skip before any expensive call, naming the requirement
- [x] coverage numbers reproducible by hand from the evidence table
- [x] SEM_THRESHOLD is measured, with derivation recorded
- [x] profile_sync.py rejects a fabricated 9 that 0.9 used to smuggle through

## Review — Phase 1 closed 2026-09-14

Shipped: 8 source files, 49 tests, 0 new dependencies.
`jobpilot add --file jd.txt` prints a per-requirement gap report and skips before any
drafting call. Rust JD -> SKIP (exit 1). Werkstudent JD -> PROCEED (exit 0).

### Bugs found and fixed while building

1. **P1 substring metric match** (`scripts/profile_sync.py`). A fabricated `9` passed on
   `0.9`, `40` on `740`, `13` on `13,582`. Now exact membership against the shared
   `expand_metric_tokens()`. 8 adversarial cases tested.
2. **`allowed_metrics` was treated as authoritative.** It is a cached render of the
   bullets and had already drifted (omits the alpha-sweep values). `all_metrics()` now
   derives from the bullets; drift is reported by `profile check`, not enforced.
3. **My own regex ate `0.3/0.5/0.7/0.9` as one token `0.3/0`**, silently swallowing three
   real values — the same false-negative class as P1. Slash-splitting added.
4. **German would have passed the gate.** The keyword set added every listed language
   regardless of level, so a "fluent German (C1)" disqualifier matched the A1/A2 entry.
   Languages now enter only at fluency. `never_claim` depends on this.
5. **Semantic false positives on real JD text.** "Production experience with Apache Kafka"
   scored 0.546 as covered with no Kafka in the profile. Real requirement texts added as
   probes; threshold recalibrated 0.5417 -> 0.5558.
6. **False skips at 100% coverage.** A Werkstudent JD skipped because "currently enrolled
   in a Master's" and "20 hours per week" had no matching terms, though both are stated in
   the profile. `_eligibility_terms()` derives the phrasings from `education` and
   `constraints`. This is the expensive direction: it drops jobs he can get.
7. **not_shipped flag was lost at the store boundary.** `Evidence.status` now carries it;
   the report marks it. Phase 2 must never phrase that bullet as delivered.

### Open, carried into Phase 2

- **SEM_THRESHOLD margin is 0.0045** (covered floor 0.5581, uncovered ceiling 0.5536). The
  semantic signal is at its useful limit; named technologies are carried by the keyword
  half. Upgrade path when a real JD is misjudged: cross-encoder rerank of the top-k before
  thresholding — not pushing the number around.
- **Golden set is 2 synthetic JDs.** Needs 3+ real postings to be worth anything as an
  eval instrument. Every threshold claim above rests on synthetic text until then.
- `allowed_metrics` drift is reported but unfixed; needs `jobpilot profile sync` to
  regenerate it (Phase 5 write-back).
- Token-level metric validation cannot bind a number to its own metric string: a real
  "61" could appear in a fabricated "61% faster". Span-level check if Phase 2 evals show
  that failure mode.

## Real-JD bug pass — 2026-09-15

Pulled 10 real postings via Apify (LinkedIn scraper), saved to `evals/real/`. Ran all 10
through `jobpilot add`. 3 false skips, 1 false pass out of 10. Fixed all five; 68 tests
now pass (was 51).

1. **Compound language requirement passed on its claimable half.** "Fluency in English and
   German is required" covered by keyword `english` alone — cleared the gate on a
   German-mandatory job (Bain). `unclaimable_languages()` + `_blocked_language()` in
   `coverage.py`: reads requirement TEXT (not just `keywords`, which the extractor had
   only emitted `english` for), blocks the whole requirement if it names a language below
   fluency, precedes both signals. Prompt rule 1 now calls out language pairs explicitly.
   Exposed a second bug in the same pass: the multi-word keyword rule required every word
   known, so "fluent in english" failed where bare "english" matched. `KEYWORD_FILLER`
   strips grading/glue words before that check.
2. **Personality traits typed `hard`.** "Genuinely interested in lithium-ion batteries",
   "you read papers and benchmarks critically", "independent working style" — none
   evidenceable by a CV line, all uncovered, all counted as blocking gaps. Caused 2 of the
   3 false skips (ACCURE, Retorio) outright, a third (Recall Space) partially. Prompt
   rules 8 ("disposition is never hard" — the test is "could a CV line prove it?") and 9
   ("or keen to start" makes a clause soft, not hard).
3. **Absence of a fact read as failure of it.** Bundesbank skipped at 100% hard coverage
   on "4th semester" and "GPA 2.5" — neither is in master_profile.yaml, nothing ever will
   put them there. `Requirement.unknown` + `UNRECORDED_FACT_PATTERNS` (semester, GPA,
   transcript, Führerschein, Führungszeugnis): an unrecorded personal fact routes to
   `open_questions()` and a `?` in the report, not a skip. `unmet_disqualifiers()` excludes
   `unknown`. German stays a real disqualifier — A1/A2 is a recorded fact, not a blank.
4. **On-site clauses never compared to where the job is.** Nothing in the gate knew
   Deepak's own location, so Mannheim/Attendorn skipped by accident (nothing matched) and
   Berlin (commutable from Potsdam) skipped for real. `location_terms()` from
   `identity.location` + `constraints.relocation`, `GENERIC_PLACE_WORDS` drops "germany"
   (every LinkedIn location ends in it — kept, it would make Munich read as commutable).
   New `--location` CLI flag; job.location empty -> `unknown`, not skip. Also: Recall
   Space's on-site clause lived in the JD's Benefits block and rule 7 (ignore boilerplate)
   ate it — prompt carved out an explicit exception for where-the-work-happens statements.
5. **Long multi-clause requirements dilute the embedding** — the 0.0045-margin problem
   from Phase 1, now confirmed on real text: "First programming experience (e.g.
   university projects, own projects, internships)" scored 0.465 against 2.5yrs production
   SWE. First tried splitting the requirement itself at extraction — fixed retrieval but
   broke the gate (inflated `uncovered_hard` counts under the same `max_blocking_hard_gaps
   = 1`; Temedica, a real PROCEED, started skipping). Reverted. Fix landed on the
   retrieval side instead: `subqueries()` splits a requirement's TEXT into clauses for
   query purposes only, `retrieve_evidence` merges best-per-bullet back onto the one
   requirement. Gate still counts it once, keyword anchor still spans the full text. Mean
   similarity rose across all 10 JDs; threshold re-verified unchanged (0.5558, 0/15).

### Still open

- Retorio's `built agents yourself` gap is real — this repo isn't in
  `master_profile.yaml`. Evidence to add, not code to fix.
- Extractor still translates German postings to English (ARAG, Mubea, Bundesbank,
  disruptive all came back English) — keywords go into the report in English while those
  companies' ATS filters on German.
- valuemize's "hybrid optional" reads as a hard on-site disqualifier and skips. No
  hybrid/remote signal in `_is_onsite`/`ONSITE_PATTERNS`.

## Review — Phase 2 closed 2026-09-15

Shipped: the full `diagnose -> rewrite -> validate_facts -> review` loop (with the
retry-on-fact-error and retry-on-low-score cycles back to `rewrite`, gated by
`attempt_count` vs `settings.max_rewrite_attempts`), Typst-based PDF rendering for the
two-column CV and cover letter (`render.py` + `templates/*.typ`), deterministic ATS
scoring (`scoring/ats.py`, 5-component rubric + fabrication gate, printed as a report
string), and a `--no-render` CLI flag that stops the graph after `review` (builds a
second, shorter-tailed compiled graph rather than branching one graph at runtime) for
cheaper iteration on prompts. `jobpilot add` now prints DIAGNOSIS / DRAFT / ATS sections
after the Phase 1 gap report, plus an `artifacts:` block naming the rendered PDF paths.
99 tests pass (was 68 going into this phase).

### Known items carried forward (real findings, not hypothetical)

1. **`_local_tech()` stack-list fallback can misattribute a job-scoped tech term to the
   wrong bullet under the same employer/project.** It grants any JD term listed in a
   job's/project's stack as "evidenced" for ANY bullet under that parent, even one whose
   own outcome text never mentions the term — a job-scoped misattribution, not a
   wholesale invented-skill leak (Task 2 minor, deferred).
2. **The ATS score's "literal keywords" component is binary (0 or 20 points)**, not a
   graduated check of verbatim keyword presence in the rendered draft — the numerator
   always equals the denominator whenever any requirement is covered. This is inherited
   verbatim from the plan's own reference implementation (task-9-brief.md Step 4), not a
   bug introduced during implementation. Ruled accepted: doesn't affect the fabrication
   gate or the apply/fix/skip threshold logic, which score independently (Task 9
   Important, ruled accepted).
3. **`score_ats()`'s precondition check uses a bare `assert`** for the `cv_pdf` artifact
   instead of a typed exception — acceptable for an internal graph-node invariant, but
   worth a typed exception if this ever runs outside a controlled graph context (Task 9
   minor).
4. **Live-tested 2026-09-15: 10 real JDs pulled via Apify (LinkedIn Jobs Scraper,
   "Working Student Data" / Germany), run through `jobpilot add` with a real
   `ANTHROPIC_API_KEY`.** Found and fixed one Critical bug immediately: `claude-sonnet-5`
   rejects any explicit `temperature=` kwarg ("`temperature` is deprecated for this
   model") — this crashed every single job that reached `diagnose` (3/10, since the
   Phase 1 gate itself correctly skipped the other 7 on real disqualifiers — German
   fluency, onsite-in-a-city-the-candidate-can't-reach). Fixed by dropping `temperature=`
   from the three Sonnet-backed nodes (`diagnose.py`/`rewrite.py`/`review.py`); Haiku
   nodes (`extract_requirements`/`classify_role`) were unaffected and needed no change.
   After the fix, all 3 re-ran cleanly end-to-end through `diagnose → rewrite →
   validate_facts` — **and the fact validator correctly caught real LLM fabrication live**
   (the rewriter claimed "machine learning"/"visualization"/"iot" with no profile
   evidence on the Mubea JD; `validate_facts` retried twice, still failed, and correctly
   routed to `log_fact_failure` instead of rendering — exactly the guardrail's designed
   behavior, observed for the first time against a real model).
5. **Fixed (same day): the fact validator's JD-keyword check was trigger-happy on
   generic single-word terms** ("data", "learning", "cloud") — `validate_facts_node`
   now only forwards HARD/DISQUALIFIER requirement keywords (soft keywords never reach
   the check), and `_local_tech`/the cover-letter's `allowed_tech` now also count a word
   as evidenced if it genuinely appears in the cited bullet's own `outcome`/`method`
   text, not just formal skill/stack entries. Re-run against the same 3 real JDs that
   originally produced the false positives (ACCURE, Mubea, Bundesbank) — all three now
   clear `validate_facts` and reach `render_documents`/`score_ats` for the first time,
   producing real PDFs (verified nonzero, valid PDF files) and real ATS scores.
6. **New finding from that first successful render+score run: `ats_score()` never
   factors in `review_score` at all.** All 3 jobs that reached scoring got a
   near-perfect ATS total (97.8, 99.1, 74.1) and 2 of 3 recommended "apply" — despite
   the `review` node (a demanding LLM judge) scoring the SAME draft only 4-5/10 on all
   three. The 5-component ATS formula (hard coverage, literal keywords, PDF
   parseability, quantification, positioning) has no path for a low review score to
   pull the total down, so a draft the judge flagged as weak can still score
   "apply." Combined with finding #2 above (the "literal keywords" component being
   binary 0/20), most of the 5 components are easy near-max once a draft merely
   clears the fabrication gate — `hard req coverage` (50 pts) is the only component
   with real spread across these 3 runs. Not fixed — this is a rubric/spec question
   (should `review_score` be a 6th component? at what weight?) for whoever owns
   plan.md §6, not an implementation bug to patch ad hoc.

7. **`highlighted_projects` is written by `rewrite` and read by nothing** — `render.py`,
   `scoring/ats.py`, and `cli.py` all ignore it. Either surface it somewhere (e.g. an ATS
   component, or a CLI print line) or drop the field — currently dead weight (final
   whole-branch review, minor, deferred).
8. **`fact_gate` returns `Literal[...]`, `review_gate` returns bare `str`** — naming/typing
   drift between the two gates that route the same retry loop. Harmless today; worth
   tightening if either gate grows more branches (final whole-branch review, minor,
   deferred).
9. **`classify_role` (an LLM call) runs again on every rewrite retry for the same JD** —
   `lru_cache` covers `_model`, not the call itself, so a review- or fact-triggered retry
   pays for an extra Haiku call to re-derive a role that cannot have changed. Cache by
   `jd_text` if retry volume ever makes this worth it (final whole-branch review, minor,
   deferred).

### Fixed in the final whole-branch review pass

Two Critical + three Important findings surfaced by the final cross-cutting review, all
fixed and re-reviewed clean before merge:
- `Draft.section_order` was LLM-freestyle despite the "deterministic dict lookup, never
  LLM freestyle" rule — `rewrite()` now forces the classifier's `order` back onto the
  returned `Draft`.
- Section order never reached the rendered PDF (Task 3's whole classifier mechanism
  changed nothing about the document) — `render.py` now builds an ordered `main_sections`
  list from `draft.section_order`, and `cv_two_column.typ` renders from it instead of a
  hardcoded projects-then-experience sequence.
- `ats.py`'s `gates_failed()` duplicated the disqualifier check and dropped the `unknown`
  carve-out, which would have zeroed the score of otherwise-strong applications with an
  unrecorded fact (e.g. a semester count) — now reuses `state.unmet_disqualifiers()`.
- Review-triggered rewrite retries were blind rerolls (weaknesses only logged to `notes`,
  never fed back to `rewrite`'s prompt) — `review()` now returns them via
  `validation_errors`, gated to only fire when a retry will actually happen (so a clean
  proceeding draft's `no_fabrication` gate is never touched by non-blocking weaknesses).
- The three LLM prompts told the model to call tools (`emit_diagnosis`/`emit_draft`/
  `emit_review`) that don't exist — `with_structured_output` names tools after the actual
  Pydantic classes (`Diagnosis`/`Draft`/`ReviewResult`); prompts now match.

### Carried into Phase 3 (per plan.md §16)

- `recruiter_sim` and `hiring_manager` nodes (roles 4 and 5 of the five-role pipeline;
  `diagnose`/`rewrite`/`review` — roles 1-3 — are done).
- SQLite persistence (a checkpointer/cost ledger — currently one job, one invocation, no
  resume).
- The human interrupt point (`§9`) — nothing to resume yet; Phase 2 runs straight
  through.

## review_score / ATS disconnect — fixed 2026-09-16

Item #6 from the 2026-09-15 review, and #4 in improvement.md. `review_score` now caps
`ats_score()`'s total at 94 (held out of `apply`) whenever it's below
`settings.min_review_score` (7) and no hard gate already failed. Cap, not a 6th
component — the 100-point weights are unchanged; see plan.md §6 "Review-score cap".
`scoring/ats.py`, `tests/test_ats_score.py` (4 new tests), `plan.md` §6. 108/108 tests pass.

## Dead-state cleanup + retry-cache — fixed 2026-09-16

Items #7, #8, #9 from the 2026-09-15 review (improvement.md #6, #1):
- `highlighted_projects` was written by `rewrite` and read by nothing — wired into
  the CLI's DRAFT report line instead of dropping it (Phase 3's recruiter_sim/
  hiring_manager or the cover letter may still want it; printing it is zero-risk).
- `review_gate` now returns `Literal["retry", "proceed"]`, matching `fact_gate`'s typing.
- `classify_role` is now `@functools.lru_cache`d by `jd_text` — a review/fact-validation
  retry no longer pays for a redundant Haiku call to re-derive a role that can't have
  changed. `src/agentic_ai/cli.py`, `nodes/review.py`, `section_order.py`. 108/108 pass.

## Token/cost logging — implemented 2026-09-16

improvement.md #1 ("no token usage or $ cost logged anywhere"). Verified current
Anthropic pricing live (claude-haiku-4-5: $1/$5 per 1M in/out, claude-sonnet-5: $2/$10)
before hardcoding rates — see `src/agentic_ai/costs.py`.

- `costs.py` — `record_usage(raw_message, model, node)` reads a LangChain AIMessage's
  `.usage_metadata` and prices it against `PRICE_PER_MTOK`; raises on an unpriced model
  rather than silently costing $0.
- `state.py` — `JobState.llm_calls` (reducer list, same pattern as `notes`) +
  `total_cost_usd` property.
- Wired into the 4 graph nodes that call the LLM: `extract_requirements`, `diagnose`,
  `rewrite`, `review` — each records usage for every API call made, including a
  parse-failure attempt that gets retried (it was still billed).
- `classify_role` (section_order.py) deliberately NOT instrumented — already
  `lru_cache`d by jd_text, and a 256-max_tokens Haiku call is cheap enough that
  threading a second return value through it wasn't worth it. Documented inline.
- `cli.py` — prints `cost: $X.XXXX (N LLM call(s))` after a run; `--verbose` breaks it
  down per node.
- `tests/test_costs.py` (4 new tests). 112/112 pass.

## Prompt caching — implemented 2026-09-16

improvement.md #1 ("no Anthropic prompt caching"). `rewrite` and `diagnose` both send
the full profile on every call — `rewrite` pays for it again on every fact-validator
and review retry (up to 2x), and it's identical across every job in a run.

- `nodes/rewrite.py` — human message split into content blocks with 2 `cache_control`
  breakpoints: after the profile+never_claim block (reusable across every job) and
  after section_order (reusable across every retry of the SAME job). System prompt
  also cached.
- `nodes/diagnose.py` — profile block cached (1 breakpoint); diagnose runs once per
  job (no retry loop back to it), so this only pays off across jobs, not within one.
- `costs.py` — `record_usage` now reads `input_token_details.cache_read` /
  `cache_creation` and prices them at 0.1x / 1.25x the base input rate instead of the
  flat rate (LangChain's `input_tokens` already folds both into the total). Without
  this the cost number would silently hide the savings caching is supposed to produce.
- `tests/test_costs.py` — 2 new tests for the cache pricing math. 113/113 pass.
- NOT yet confirmed against a live API call (`cache_read_input_tokens > 0`) — unit
  tests cover the plumbing/pricing, not an actual cache hit. Costs real money to
  verify; deferred until asked.

## Phase 3 — implemented 2026-09-16

Full 5-role graph + human loop + persistence, per plan.md §16 Phase 3's 3 bullets
(plan at ~/.claude/plans/frolicking-finding-sparkle.md).

1. **`recruiter_sim`** (`nodes/recruiter_sim.py`, Haiku, role 4) — shallow keyword-
   literal screen over the rendered CV text (not the profile) + hard requirements only.
   `recruiter_gate`: `hard_fail` → `log_recruiter_fail` → END (skip before render, per
   CLAUDE.md "Hard-fail = don't recommend applying without fixing the gap first");
   `pass`/`soft_fail` → `hiring_manager`.
2. **`hiring_manager`** (`nodes/hiring_manager.py`, Sonnet, role 5) — full profile +
   draft, judges fit/tone/defensibility ("could you defend this under a follow-up
   question"). Informational only — doesn't gate the graph; the deterministic
   `AtsScore` stays the authoritative apply/skip number, this is what the human sees
   at review time.
3. **Checkpointer + interrupt (§9)** — `graph.py`: `build_graph()` takes an optional
   `checkpointer`; when set, compiles with `interrupt_before=["render_documents"]`.
   New `run_for_review()` / `resume_review()` / `get_paused_state()`, all using
   `SqliteSaver.from_conn_string(settings.checkpoint_db_path)`, `thread_id=job.id`.
   `run()` (plain `jobpilot add`, no `--review`) has no checkpointer — unchanged
   behavior, just 2 more LLM calls now that recruiter_sim/hiring_manager are wired
   into the default (non-`skip_render`) path.
4. **`jobpilot add --review`** pauses before render, prints the job id.
   **`jobpilot review <id>`** prints diagnosis/recruiter/hiring_manager/draft, prompts
   y/n (edit deferred — noted in the plan as a distinct follow-up feature), resumes or
   marks rejected.
5. **SQLite persistence** (`db/schema.sql`, `db/repo.py`) — `jobs` (upserted) + `runs`
   (cost ledger: tokens_in/out, cost_usd from `state.total_cost_usd`, coverage,
   verdict, skip_reason). `requirements`/`applications` tables deliberately deferred
   to Phase 5 (outcome tracking) per plan.md's own build order — not in Phase 3's
   bullet list. `persist_run()` called from both `add` paths and `review`.

Verified: `skip_render=True` path (Phase 1/2's `--no-render`) is byte-for-byte
unchanged — no new nodes added to that branch, so all prior tests still cover it
untouched. The interrupt/pause/resume mechanics were verified end-to-end against a
REAL `SqliteSaver` + REAL Typst render (not mocked) in `test_graph_phase3.py`, with
only the 6 LLM-calling nodes monkeypatched to stand-ins — zero API cost, but real
proof the checkpointer/thread_id/resume plumbing works, not just an assumption from
reading the LangGraph docs.

124/124 tests pass (11 new: 4 pure `recruiter_gate` tests, 4 interrupt/resume
mechanics tests, 3 `db/repo.py` tests).

**Not done, explicitly deferred** (see the plan file's Scope section):
- `jobpilot cost` reporting command, `MAX_DAILY_COST_USD` budget guard, structured
  JSON logs (all §12, not in Phase 3's bullet list)
- Langfuse tracing (explicitly Phase 4)
- "edit" in the review flow (only y/n implemented)
- Live end-to-end verification against the real Anthropic API (`add --review` →
  `review <id>` → y) — costs money, not run yet, ask before running.
