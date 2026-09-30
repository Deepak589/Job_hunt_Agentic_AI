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
- Render: **Typst** (`typst-py`), not `docxtpl` — deterministic two-column PDF.
- Config `pydantic-settings`, env prefix `JOBPILOT_`, `.env` never committed. CLI `typer`.
- `pytest` · `ruff` (line-length 100) · `mypy --strict` + pydantic plugin.

## Pipeline
```
load_job → extract_requirements → retrieve_evidence → score_coverage
  ├─ hard gap → log_skip → END
  └─ diagnose → rewrite → validate_facts
       ├─ fail → log_fact_failure → END
       └─ review (max 2 loops, min score 7) → recruiter_sim
            ├─ hard-fail → log_recruiter_fail → END
            └─ hiring_manager → [human_review: interrupt()] → render_documents → score_ats
```

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
- **Per-requirement matching, never blob cosine similarity.** Blob similarity only proves both
  texts are tech documents and yields no actionable gap report.
- **The hard-gap gate is categorical, not a threshold.** A named hard requirement with zero
  evidence is a skip; rewriting cannot fix it (`max_blocking_hard_gaps`, currently 1).
- **`validators/facts.py` is deterministic Python, never an LLM.** Highest-value guardrail here.
  Extend it with adversarial cases — especially tech named in the JD but absent from the profile.
  Never route it through a model, never soften it to make a run pass.
- **`extract_requirements` and `recruiter_sim` are stateless on purpose.** Give the extractor the
  profile and it finds the requirements it expects, destroying the gap analysis. A real first-pass
  screen has no context either.
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
- [ ] Backpressure limits (`plan.md` §19.2) are designed but **unimplemented** —
      `max_pending_review` 10, `max_tailored_per_day` 6, `max_per_company_days` 30. Nothing in
      `src/` enforces them, so the batch runner can outrun review. Check per job at claim time.
- [ ] Assisted-apply (Phase 6) — proposed, not yet in `plan.md`
- [ ] Delete the deprecated `requirements.txt`; resolve the 2 self-cycles the graph reports
      (`budget.py`, `db/repo.py`)
