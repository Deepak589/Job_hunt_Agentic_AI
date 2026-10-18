# JobPilot — Agentic_ai

Multi-agent pipeline: JD in → evidence-backed fit score → tailored CV → rendered package.
**It never applies.** Every run stops at a human checkpoint.

This file is session context only. Pipeline *behaviour* lives in `prompts/` (wording, CV rules),
`validators/facts.py` (enforcement), `data/master_profile.yaml` (`never_claim`, `allowed_metrics`)
and `plan.md` (rubrics, § numbers). Never copy those rules here — two copies drift.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

## At session end — update the graph
If code, prompts or docs changed, run `graphify update .` (no API cost) so the graph matches the
commit. If it was skipped, say so in the closing summary.

## Stack
- Python **3.13**, `uv` (`uv.lock`, `uv_build`). `requirements.txt` is a DEPRECATED pointer — delete it.
- LangGraph 0.6+, `langchain-anthropic` + `langchain-core` 1.0. **Anthropic only** — Groq and OpenAI
  were dropped deliberately; a second provider costs another SDK, auth path, rate-limit regime and
  set of structured-output quirks for marginal savings.
- Retrieval: local `sentence-transformers` / **`BAAI/bge-m3`** (multilingual — German JDs appear in
  English-role searches), Chroma at `data/chroma`, reranker `ms-marco-MiniLM-L-6-v2`.
- Storage: **stdlib `sqlite3`, no ORM** — `data/jobpilot.db` (jobs, runs, cost) and
  `data/checkpoints.db` (LangGraph `AsyncSqliteSaver`, WAL).
- Ingest: `httpx`, `tenacity`, `trafilatura`, `extruct`, `lingua`. No headless browser.
- Render: **HTML (Jinja2) + headless Chrome** `--print-to-pdf`, not `docxtpl`/Typst — same
  approach as the Job_finder repo's `render_cv.sh`, so both CV pipelines render the same way.
- Config `pydantic-settings`, env prefix `JOBPILOT_`, `.env` never committed. CLI `typer`.
- `pytest` · `ruff` (line-length 100) · `mypy --strict` + pydantic plugin.

## Pipeline
Slimmed (branch `slim-agent`) to match the manual flow in `Examples/CLAUDE.md`: one reasoning
call, deterministic guards around it, a human as the reviewer.
```
load_job → agent_cv (ONE LLM call: requirements + verdicts + full draft + 3 judgments)
  ├─ hard gap / unmet disqualifier → log_skip → END
  └─ validate_facts (deterministic, BLOCKING; retry max 2 → rewrite, never agent_cv)
       ├─ fail → log_fact_failure → END
       └─ [human_review: interrupt()] → render_documents → score_ats
```
`settings.use_agent_cv` (default True) picks who drafts: `agent_cv` (one call) or the
two-call `analyze` → `rewrite`. It moves one node and nothing else — both topologies share
the gate, the blocking validator, the review interrupt, the render/score tail, and the
`run`/`run_many`/`run_for_review` entry points, so `record_run`, dedupe, the cost ledger,
`preferences.yaml` and MCP review visibility apply to either.
Removed: separate extract / evidence-retrieval / coverage / judge / diagnose nodes, and the LLM
`review`, `recruiter_sim`, `hiring_manager` nodes (originals are on `main`). Kept in code after
`analyze`: the language-below-fluency and on-site-location rails (`coverage.py`), the categorical
gate, `validate_facts`, rendering, ATS score. `rewrite` must include every EXPERIENCE bullet and
at most `max_bullets_per_project` (2) per project, reordering by JD relevance and never merging
(`prompts/rewrite.md`, same wording in `prompts/agent_cv.md` — keep them in sync).

## Commands
```bash
uv sync --extra dev
jobpilot add --file jd.txt | --url <posting> | --dir <folder>   # concurrency 3
jobpilot review <job_id> [-v]        # approve / edit / reject
jobpilot resume <job_id> | applied <job_id> | outcome <job_id> ...
jobpilot cost [--days N] | judge-stats
jobpilot index build [--force] | index calibrate
jobpilot profile init | profile check        # drift gate, run before any CV build
jobpilot source arbeitnow | source adzuna | run
./build_cv.sh                                # cv_data.json → main_cv_v2.pdf
pytest -q && ruff check . && mypy src        # + pytest evals/ for the golden set
```
Manual JD input is the primary path; ingest is the supplement.

## Do not undo — each cost something to get right
- **`agent_cv` is a NODE, not a second entry point.** It began as `jobpilot agent-draft`,
  drafted better than `analyze` + `rewrite` and cost less — and reached none of the
  pipeline: no run row, no content-hash dedupe, no review checkpoint, no preferences, no
  per-job budget reserve, invisible to `list_pending_reviews`. Worse, its `validate_facts`
  ran in REPORT mode, which looks like a gate and is not: on `good_jd` it reported 5 fact
  errors and still returned `apply`. Never re-plumb a drafting path around the graph to
  pick up a better prompt; move the call inside and keep the guarantees. `agent-draft`
  stays as the comparison harness and stays report-only — the thing being measured must
  not change underneath it.
- **A fact error retries through `rewrite`, never back through `agent_cv`.** Re-running the
  one call would re-pay for requirement extraction and could return a DIFFERENT requirement
  list after `hard_gap_gate` had already ruled on the first one — a gate decision
  invalidated by its own retry. `rewrite` gets the diagnosis and the exact errors and fixes
  only what they name.
- **The rails fire on what a requirement DOES, not on the bucket the model gave it**
  (`analyze._GATED_TYPES`). Scoping `_apply_rails` to `type == "disqualifier"` made both
  rails depend on the model labelling correctly, and `agent_cv` — which types and drafts in
  one call — filed good_jd's "Pullach, nearby Munich" on-site clause and its English clause
  as `hard`. That silently skipped both: `_location_reachable` never ran, and a German
  demand filed `hard` would have bypassed the language rail into the hard-gap budget. SOFT
  is deliberately left alone; it only moves a coverage number.
- **`validators/aliases.py` is a scoped resolver, not a whitelist.** An umbrella a JD uses
  ("LLMs", "Generative AI", "Agentic AI") is absent from the profile while the work it names
  is present — there is no `LLM` skill, there is LangChain, LangGraph, RAG, Ollama. Each row
  resolves against THE SAME scope the term was already checked in, so a bullet built from
  the insurance migration still cannot say "LLM", and an umbrella whose backing work leaves
  the profile stops passing on its own. Never add a row whose right-hand side is not already
  a profile term — `test_every_alias_target_is_a_real_profile_term` fails first if you do.
- **A list of alternatives is never a list of hard requirements** (`prompts/analyze.md` and
  `prompts/agent_cv.md`, same wording in both — keep them in sync). "A or B", "A/B",
  "such as A, B", a list ending in "etc", "e.g.", "including": any ONE satisfies it, so the
  capability is one item marked met and each unheld option is `soft`, never a hard item of
  its own. `agent_cv.md` originally compressed this rule and dropped the "such as ..." case,
  and a railway-AI posting (2026-10-17) written entirely in the dropped forms came back as
  16 hard requirements with 6 "gaps" — Llama-Index, FAISS, MongoDB, PyMuPDF, Docling,
  BeautifulSoup — four of which the profile satisfies through LangChain, Chroma and
  PostgreSQL. `hard_gap_gate` then skipped a job the model itself scored `apply`. The tell:
  "Tensorflow **or** Pytorch" was the one list written with "or", the one form the prompt
  still covered, and the only one typed correctly. Do not answer a false skip by raising
  `max_blocking_hard_gaps` — against 16 hard items a bigger cap does not loosen the gate, it
  deletes it, and leaves the typing wrong in the direction that passes jobs that should skip.
- **Naming a gap is not claiming it** (`validators/facts._disclaimed`). The CV rules REQUIRE
  the cover letter to state a missing hard requirement plainly. Check 3 was a bare whole-word
  regex over the whole letter, so "I haven't worked with LlamaIndex, FAISS, MongoDB, PyMuPDF,
  Docling or BeautifulSoup specifically" scored one unevidenced-tech error per name — five
  blocking errors on a true sentence, enough to burn both retries and end at
  `log_fact_failure`. The exemption is deliberately narrow and its limits are what make it
  safe: cover letter only (a bullet is a positive claim about a cited accomplishment and has
  nothing to disclaim), the cue must PRECEDE the term in the SAME sentence, and bare "no"/
  "not" are not cues — "Built the pipeline, not the prototype, on FAISS" must still fail.
  `tests/test_negation_guard.py` pins every one of those limits; never widen the cue list
  without adding the adversarial case that proves the gate still bites.
- **The project-bullet cap is per PROJECT, and a selection rather than a truncation.**
  `max_bullets_per_section` (20) cannot express it: every project lands in the one "projects"
  section, so 20 never fired on 17 project bullets across 5 projects and a five-bullet project
  crowded out a two-bullet one. `_capped_per_project` keeps the first
  `max_bullets_per_project` (2) per `parent_id` in the model's emitted order, which both
  prompts define as strongest-and-most-JD-relevant first — so order IS the selection
  criterion, and changing the prompts' definition of that order changes which bullets ship.
  EXPERIENCE is deliberately uncapped (4 bullets across 2 roles; work history is read first).
  The prompts state the cap so the model picks two deliberately; the code is the backstop for
  when it does not, and a bullet with an unknown `source_bullet_id` is always kept so
  `validate_facts` can still report it. Costs ats.py's "literal keywords" (20 pts), which
  counts evidenced JD terms appearing verbatim in the rendered PDF — hence the prompts' "by
  JD-term coverage first". "hard req coverage" (55 pts) and the gate are unaffected: `analyze`
  judges requirements against the whole profile, never against the draft. Restore the old
  behaviour with `JOBPILOT_MAX_BULLETS_PER_PROJECT=20`; do not instead delete the cap, and do
  not cap experience to make a page fit.
- **Per-requirement matching, never blob cosine similarity.** Blob similarity only proves both
  texts are tech documents and yields no actionable gap report.
- **The hard-gap gate is categorical, not a threshold.** A named hard requirement with zero
  evidence is a skip; rewriting cannot fix it (`max_blocking_hard_gaps`, currently 3 — raised
  from 1 because `analyze` now judges every hard requirement against the full profile, so what
  reaches this count is a higher-confidence real gap, not a keyword miss).
  SOFT coverage stays pure keyword+semantic, untouched. On the agent path the gate runs
  AFTER a draft exists, because one call returns both: a skipped job costs the full
  drafting call (~$0.18) rather than `analyze` alone (~$0.05), and a crash inside that one
  call banks no spend at all, since there is no earlier completed node to have recorded it.
  Set `JOBPILOT_USE_AGENT_CV=false` for a skip-heavy batch.
- **`validators/facts.py` is deterministic Python, never an LLM.** Highest-value guardrail here.
  Extend it with adversarial cases — especially tech named in the JD but absent from the profile.
  Never route it through a model, never soften it to make a run pass. It only ever receives HARD
  keywords now — a DISQUALIFIER is already proven true by `hard_gap_gate` before a draft exists,
  so re-checking one here was a category error (Bugs.md #2), not a case this file should extend.
- **`analyze` (one LLM call) decides coverage; code overrides it on two known blind spots.**
  `unmet` needs a quoted contradiction in the profile (disqualifier) or a stated absence (hard
  item) — never silence. A tool/fact the profile never mentions comes back `unknown`, which asks
  at review (`open_questions`) instead of gapping. Code then re-decides the language demand
  (`languages.language_verdict`, via `coverage.language_requirement_verdict`) and any on-site
  clause (`_location_reachable`), whatever the model said. A DISQUALIFIER `unmet` is an
  immediate skip; a HARD `unmet` joins the `max_blocking_hard_gaps` budget.
- **A language demand never skips a job.** The demand the JD states is parsed to a CEFR level
  and compared against the level the profile holds (`languages.py`): `met` at or below it,
  `unknown` above it. `max_language_gap_steps` ships at 5 — the full width of the scale — so
  `unmet` is unreachable for a language and every German demand reaches human review instead,
  with the cover letter carrying one line: "My German is at A2 and I am currently learning
  B1." That sentence is mandatory in `prompts/rewrite.md` and `prompts/agent_cv.md` whenever a
  posting names German, and silence about it is treated as worse than a modest line. Policy,
  not laxity: a stated German requirement is frequently softer than it reads on Berlin
  postings, and a silent skip removed the choice. Set `JOBPILOT_MAX_LANGUAGE_GAP_STEPS=1` to
  make anything more than one step above the profile a skip again. The guard that remains is
  that a demand above the held level is never marked *met* — the weakest language in a
  compound requirement still decides ("Fluency in English and German"), so an any-match
  keyword hit on `english` cannot satisfy the German half. The weakest language in a compound requirement still decides
  ("Fluency in English and German"), which is why the any-match keyword hit alone never
  settles a language. `validators/facts.py` polices the level a draft CLAIMS, not whether it
  mentions the language: the profile's own level may be stated plainly, and its `in_progress`
  level when framed as in progress ("A2 completed, working toward B1"). Country names are not
  language mentions — "authorized to work in Germany" is not a German requirement.
- **Deliberate reversal of the old "extractor never sees the profile" rule.** `analyze` reads JD
  and profile together, as the manual flow in `Examples/CLAUDE.md` does. The risk is the one the
  old rule guarded against: the model listing requirements it expects the candidate to have.
  Mitigation: the JD is sent first, and the prompt says the list comes from the JD alone. Watch
  for it in `evals/` (a golden JD whose requirement count drops after this change is the tell).
- **`interrupt()` requires a checkpointer.** `human_review` is a dynamic node, not
  `interrupt_before` — that was a real runtime bug in v1.
- **No supervisor agent.** The LangGraph edges *are* the orchestration; every routing decision is
  encodable (gate = boolean, review loop = threshold, attempts = counter). The answer to "should
  batching be an agent" is `batch.py` / `runner.py`.
- **No code path that sends, submits or posts.** The human checkpoint is structural, not a setting.
- **No LLM node writes long-term memory.** Writes are deterministic (episodic/outcome SQLite) or
  human-approved (`preferences.yaml`, only from review edits).
- **`sem_threshold` (0.5558) is calibrated, not guessed** — 15 probes, margin 0.0045. Named tech
  rides the keyword half of §6; semantic is the paraphrase catcher. If a real JD is misjudged,
  rerank top-k — do not push the number around.
- **`master_profile.yaml` mirrors the CV, not the reverse.** Truth is `main_cv_v2.pdf` from
  `data/cv_data.json`; on disagreement fix the yaml. `scripts/profile_sync.py` enforces it — new
  spellings go in its `ALIASES` dict, never loosen the matcher.
- **Cowork is the interface layer only** (scheduled run, review artifact, notifications). It shells
  out to the CLI; graph logic there would be unversioned and untestable.

## Conventions
- Nodes are pure functions of state. No globals, no hidden IO.
- Type hints everywhere, pydantic models for all state and tool IO. `mypy --strict` must pass.
- **Prompts live in `prompts/`, never inline.** Change one → run the golden set.
- **Every LLM output feeding another node is schema-constrained** (`with_structured_output`,
  `method="json_schema"`). No free-text handoffs.
- Role type is LLM-picked, section order is a fixed dict (`section_order.py`) — never let the model
  freestyle it.
- Thresholds, limits and model ids are `config.Settings` fields, never literals in a node.
- Retry with backoff only on genuinely retryable failures (truncated JSON on `max_tokens`); fail
  loudly otherwise and log with `job_id`.
- Log tokens and cost per run (`costs.py`, `budget.py`); honour `JOBPILOT_MAX_DAILY_COST_USD`.
- Dedupe by content hash, not URL — postings are reposted weekly under new URLs.
- Never commit `.env`, `data/master_profile.yaml`, `data/cv_data.json`, `data/*.db`, the CV PDF or
  anything in `out/`. Update the `.example.*` files instead.
- Small commits, conventional messages (`feat:`, `fix:`, `refactor:`).

## Models
Routing lives in `src/agentic_ai/config.py` (`plan.md` §12); no model name is hardcoded in a node.
**Haiku** for schema-constrained extraction (`extract_requirements`, `role_classifier`,
`recruiter_sim`), **Sonnet** for judgment (`diagnose`, `rewrite`, `review`, `hiring_manager`).
Never collapse to one model — Opus on requirement extraction is ~10x cost for no quality gain.
Embeddings stay local, keeping the stack to one API key.

## Definition of done
`pytest -q`, `ruff check .`, `mypy src` clean · `jobpilot profile check` exits 0 · golden set no
worse than baseline · hard coverage 1.0 at the gate, zero fabricated claims past `validate_facts` ·
`plan.md` and this file updated if a decision changed · `graphify update .` run.

## Current focus
- [ ] `evals/golden/real_railway_ai_agents_alternation.txt` is checked in but has **no
      baseline entry** — run `python evals/run_harness.py --update-baseline` to pin it. It is
      the only fixture that exercises the slash / "such as" / "etc" typing rule.
- [ ] Re-run the two-JD comparison now that both paths are inside the graph, and widen it
      past two JDs — the agent path's requirement TYPING is what still differs (it filed 3
      eligibility gates as `hard` where `analyze` filed them as disqualifiers; same
      requirements, different buckets). The rails no longer care, but
      `max_blocking_hard_gaps` counts `hard`, so the typing still moves the gate.
- [ ] Backpressure limits (`plan.md` §19.2) are designed but **unimplemented** —
      `max_pending_review` 10, `max_tailored_per_day` 6, `max_per_company_days` 30. Nothing in
      `src/` enforces them, so the batch runner can outrun review. Check per job at claim time.
- [ ] Assisted-apply (Phase 6) — proposed, not yet in `plan.md`
- [ ] Delete the deprecated `requirements.txt`; resolve the 2 self-cycles the graph reports
      (`budget.py`, `db/repo.py`)
