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
4. **No live end-to-end run of the full LLM pipeline has been performed yet, in ANY task
   of this implementation.** Every LLM-calling node (`diagnose`, `rewrite`, `review`) was
   built and unit/structurally tested against stubs and fixtures, and the Typst render +
   ATS score were verified against real generated JSON — but the actual generated CV/
   cover-letter quality, and the full graph's live behavior against a real JD, are
   unverified pending an `ANTHROPIC_API_KEY`. **This is the single most important thing
   for whoever picks this up next to do FIRST** — run
   `jobpilot add --file evals/golden/real_temedica_ws_agentic.txt --location "Munich, Germany"`
   with a real key configured and read the output critically.

### Carried into Phase 3 (per plan.md §16)

- `recruiter_sim` and `hiring_manager` nodes (roles 4 and 5 of the five-role pipeline;
  `diagnose`/`rewrite`/`review` — roles 1-3 — are done).
- SQLite persistence (a checkpointer/cost ledger — currently one job, one invocation, no
  resume).
- The human interrupt point (`§9`) — nothing to resume yet; Phase 2 runs straight
  through.
