# JobPilot — System Design Review (2026-09-18)

Scope: `src/agentic_ai/` (3,474 LOC, 139 tests), `plan.md`, `improvement.md`, `tasks/todo.md`.
Question: is the current design fit for a **real-time** (always-on, self-feeding) job pipeline, and what breaks first.

Verdict up front: **the tailoring core is solid; the system around it is a CLI, not a service.** Every "real-time" failure below is in ingestion, scheduling, dedup, budget and learning-loop — not in the graph.

---

## 1. What exists (verified in code)

| Layer | State | Notes |
|---|---|---|
| Graph | ✅ 15 nodes, 3 gates, 2 retry loops | `graph.py` — deterministic gate before any Sonnet call |
| Evidence store | ⚠️ 20 CV bullets only | Chroma + bge-m3; repo READMEs (plan §5.3) never ingested |
| Coverage | ✅ 2-signal (cosine + keyword) | sem margin **0.0045** — semantic is at its limit; keyword carries |
| Fact validator | ✅ 4 deterministic checks | strongest part of the system |
| ATS score | ⚠️ 3 of 5 components ~constant | see §3.4 |
| HITL | ⚠️ static `interrupt_before` + `SqliteSaver` | only on `--review`; plain `run()` has **no checkpointer** |
| Sourcing | ⚠️ arbeitnow (page 1, no dedup), adzuna (untested) | no company ATS watcher, no scheduler |
| Persistence | ✅ jobs + runs ledger, JSONL log | `applications` / outcome tables not built |
| Cost | ✅ per-call, cache-aware; daily cap | cap checked **once per invocation**, not per job |
| Eval | ⚠️ snapshot regression only | no ground truth, no outcome feedback |

---

## 2. Real-time failure modes (ranked by blast radius)

### 2.1 Duplicate spend — no "seen" tracking
- `job_id = sha256(jd_text)`. Same posting re-fetched with one HTML edit → new id → full Sonnet run again.
- `jobpilot source arbeitnow --run` has no check against `jobs` table. Polling every hour re-runs every posting every hour.
- **Fix**: canonical key = `(source, url|slug)`; `content_hash` only for change detection. Skip if `jobs.id` exists and `runs.verdict` not null. Store raw JSON (plan §4.2 "always store raw") — not done.

### 2.2 Budget cap is per-invocation, not per-job
- `cli._check_budget()` runs once before `run_many`. `--dir` with 40 JDs at $0.30 each blows through a $2 cap.
- plan §19.3 already says `guard.claim()` per job. Not implemented.
- **Fix**: move the check into `_run_one` inside the semaphore; also count reserved (in-flight) spend, not just persisted.

### 2.3 Crash = lost money
- `run()` compiles with `checkpointer=None`. A 429-exhaustion or OOM at `hiring_manager` throws away ~6 paid calls.
- `SqliteSaver.from_conn_string` opened per call, closed per call — fine for CLI, wrong for a daemon (connection churn, no WAL).
- Docs now say static `interrupt_before` is **not recommended** for HITL; use `interrupt()` inside a node + `Command(resume=...)`, drive via `stream(..., version="v3")` and read `stream.interrupts`.
- `durability` mode not set. Default is `"async"` (small crash window). For nodes with paid side-effects (LLM calls, PDF writes) use `"sync"`.
- **Fix**: always compile with a checkpointer; `thread_id = job.id`; move render/PDF write into an idempotent guard (re-run on resume is documented behaviour: "the node re-runs from the beginning").

### 2.4 Sync nodes under `ainvoke`
- All nodes are `def`, all LLM calls `.invoke`. `run_many` → LangGraph pushes them to a thread pool. Works, but: bge-m3 CPU encode + Chroma `PersistentClient` + `Profile.load()` (re-parses YAML every node, not cached) all run N× in threads.
- **Fix**: `async def` nodes + `.ainvoke`; cache `Profile.load()` (`lru_cache` keyed on file mtime); one process-wide embedder.

### 2.5 Reranker runs on every query, result discarded
- `retrieve_many` always calls `rerank()` (cross-encoder forward pass per requirement × top_k). `coverage.retrieve_evidence` then re-sorts by cosine and ignores `rerank_score`.
- Calibration says rerank is *worse* (14/15 vs 15/15). So it's pure CPU cost on the hot path.
- **Fix**: `rerank=False` default in `retrieve_many`; only `calibrate()` opts in.

### 2.6 Ingestion is page-1-only, no scheduling
- arbeitnow: no pagination, no `since`, client-side filter → misses anything not on page 1.
- No cron/daemon. plan §19 runner + §20 Cowork scheduled task not built.
- `lingua`, `trafilatura`, `extruct`, `tenacity` are declared deps but **unused** — lang detection is a 14-word stopword heuristic.
- **Fix**: see §4.1.

### 2.7 Learning loop is open
- `jobpilot review <id>` edit → YAML → checkpoint. The **diff is thrown away**. plan §21.4 (procedural memory from edits) not built.
- No `applications` table → no outcome (interview / reject / ghost) → `max_blocking_hard_gaps`, `sem_threshold`, `min_review_score` can never be tuned on real signal (plan §18.4 says "tune after 30 runs" — nothing records the 30).

---

## 3. Design-quality issues (not real-time, but wrong)

### 3.1 LLM-judge nodes are unvalidated judges
- `review`, `recruiter_sim`, `hiring_manager` = 3 single-sample LLM judges with no calibration.
- Research (§5): exact-match agreement overstates judge reliability by 33–41 pts (κ); judges show position bias >0.10 while being test-retest consistent >0.95 — i.e. **consistently wrong**; more capable examinees get more lenient scores.
- `review_score < 7` caps ATS at 94 → a judge with unknown validity gates the apply decision.
- **Fix**: (a) recruiter_sim is keyword-literal by spec — make it **deterministic** (JD hard keywords ∩ rendered PDF text), delete the Haiku call; (b) for `review`, sample 3× or 2 models and take calibrated weighted vote (label-free WMV from Zhang et al. 2026); (c) log judge outputs against your own approve/edit/reject to measure κ before trusting the cap.

### 3.2 Structured output via `function_calling` + hand-rolled retry
- Every node: `with_structured_output(..., include_raw=True)` + 2-attempt loop on `parsing_error`. Each failed parse is billed.
- Native structured outputs (grammar-constrained, `output_config.format` / `strict: true`) are GA on Haiku 4.5 and Sonnet 5: "no retries needed for schema violations".
- **Fix**: `with_structured_output(..., method="json_schema")` — verify langchain-anthropic ≥1.5.6 maps it to native `output_config`; else call the SDK `messages.parse()` directly in `llm.py`. Keep retry only for `stop_reason in (max_tokens, refusal)`.

### 3.3 Prompt cache: 5-min TTL, Haiku never cached
- `rewrite`/`diagnose` use `{"type":"ephemeral"}` = 5 min. A scheduled batch that runs 3 jobs then idles 20 min pays full write price again.
- Haiku 4.5 minimum cacheable prefix is **4,096 tokens**; the extract prompt is below that → cache silently never fires (no error). Sonnet 5 min is 1,024.
- **Fix**: `{"type":"ephemeral","ttl":"1h"}` on the profile block (2× write, 0.1× read — pays off after 2 hits); add a `cache_read == 0` warning in `record_usage` so silent non-caching is visible.

### 3.4 ATS score is mostly `hard_coverage` in disguise
- `literal keywords` = `20 * len(x)/max(len(x),1)` → **20 whenever ≥1 evidenced term**. Constant.
- `positioning` = 5 whenever `section_order` set → always 5.
- `quantification` ≈ 10 in practice.
- `pdf parseability` checks **profile skill names** in the PDF, not **JD terms**. That is not what an ATS filters on.
- Net: `total ≈ 50·hard_cov + 35 + 15·pdf`. With gate at ≤1 uncovered hard gap, a 5-hard-req JD with 1 gap = 40 + 50 = 90 → `fix_then_apply` by arithmetic, regardless of the draft.
- **Fix**: `literal keywords = 20 · |evidenced JD terms ∩ PDF text| / |evidenced JD terms|` (post-render, on extracted text — this is the actual ATS question); drop `positioning` or make it "profile_line mentions ≥1 hard keyword".

### 3.5 Extractor translates German JDs
- Known (todo "Still open"): German postings come back English → keywords in English while the employer's ATS filters German. Also `Job.lang` detected by stopwords, never used by the gate (plan §17 prefilter not built).
- **Fix**: use `lingua` (already a dep); pass `lang` into extract prompt: "emit `keywords` in the JD's language, `text` in English".

### 3.6 Retrieval store is over-engineered for 20 vectors, under-built for the plan
- Chroma for 20 rows = a persistent process dependency for what `numpy @` does in 1 line. Fine to keep **only if** §5.3 repo ingestion lands (hundreds of chunks). Otherwise delete Chroma.
- Repo READMEs/docs are the evidence that would close "built agents yourself" gaps (Retorio). CLAUDE.md rule 5 says fetch repo docs — code never does.

### 3.7 Misc
- `classify_role` cost not recorded (documented, but "cheap" × 40 jobs/day adds up).
- `render_documents` writes `_render_<id>_cv.json` into `data/` then deletes — put it in `tempfile`.
- `out/<job_id>/cv.pdf` overwritten on re-run; no version history for a job you already sent.
- `--dir` exits 1 if *any* job skipped — a cron will read every night as failure.

---

## 4. Target architecture for "real-time"

```
 sources (arbeitnow, adzuna, Personio XML, Greenhouse, Lever, Ashby)
        │  poll (cron / Cowork scheduled task, hourly)
        ▼
 ingest: normalize → lang detect (lingua) → canonical id → diff vs jobs table
        │  new/changed only
        ▼
 prefilter (§17, deterministic, 0 tokens): lang, location, employment_type
        │
        ▼
 queue (SQLite `pending`, ordered by cheap semantic_fit)
        │  LimitGuard.claim() per job  (cost, pending_review, per-company)
        ▼
 tailor_graph (checkpointer always on, durability="sync", interrupt() node)
        │
        ▼
 review surface (Cowork artifact / notification) → approve/edit/reject
        │                       │
        ▼                       └─► edit-diff → preferences.yaml (§21.4)
 applications table ← outcome (interview/reject/ghost) → threshold tuning
```

### 4.1 Ingestion — concrete
- `JobSource` protocol (plan §4) + one class per ATS. Public no-auth endpoints, verified 2026:
  - Greenhouse `boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`
  - Lever `api.lever.co/v0/postings/{site}?mode=json`
  - Ashby `api.ashbyhq.com/posting-api/job-board/{name}`
  - Personio `{company}.jobs.personio.de/xml?language=en` (XML — dominant in German SMEs; highest value for Werkstudent)
  - Workable `workable.com/api/accounts/{sub}?details=true`, Recruitee `{co}.recruitee.com/api/offers/`
- `config/companies.yaml` (20–30 Berlin targets). Diff posting IDs against `company_snapshots`; 3 consecutive fetch failures → notify.
- URL paste path: JSON-LD `JobPosting` via `extruct` → `trafilatura` fallback (both already installed, both unused).

### 4.2 Cost — batch what isn't interactive
- Nightly pipeline is not latency-sensitive → **Message Batches API: 50% off all tokens, caching stacks on top**, structured outputs supported. Run extract + diagnose + rewrite for the whole queue as one batch; only `--review` interactive runs use the live API.
- Expected: ~$0.30/job → ~$0.12/job (batch + 1h cache on the 5–9k-token profile prefix).

### 4.3 Durability
- `build_graph(checkpointer=SqliteSaver(conn))` always; one long-lived connection with `PRAGMA journal_mode=WAL`; Postgres only if you ever run >1 worker host.
- Replace `interrupt_before` with `interrupt({"draft": ...})` inside a `human_review` node; resume with `Command(resume={"action": "approve"|"edit", "draft": ...})`. Re-run `recruiter_sim`/`hiring_manager` after an edit (currently documented as stale).
- `durability="sync"`; render node checks `cv_pdf.exists() and hash matches` before recompiling.

### 4.4 Evaluation — close the loop
- Add `applications(job_id, sent_at, cv_pdf_sha, outcome, outcome_at)`.
- Judge validation: log `review_score`, `recruiter.result`, `hiring_manager.verdict` next to your approve/edit/reject; compute Cohen's κ monthly. Under 0.4 → the judge is noise, drop it from gating.
- **Presentation-invariance test** (from Chen & Xiao 2026): render the same draft with 2 section orders / bullet orderings, assert `ats_score` and `recruiter_sim` don't flip. If they flip, the score is measuring layout, not fit.
- Keep `run_harness.py` snapshot; add ≥1 labeled probe per real misjudgment (already the stated policy — enforce in CI).

---

## 5. Research pulled (arXiv, 2025–2026) — why each matters here

| Paper | Use in JobPilot |
|---|---|
| **Why Do Multi-Agent LLM Systems Fail? (MAST)** — Cemri et al., 2503.13657 | 14 failure modes / 3 classes (spec & design, inter-agent misalignment, task verification). Your graph avoids most inter-agent modes by design (no LLM supervisor, typed state). Weakest class here: **verification** — 3 unvalidated judges. |
| **Reliability without Validity** — Norman, Rivera, Hughes, 2606.19544 (Jun 2026) | 21 judges, 541k judgments: κ 33–41 pts below exact-match; position bias >0.10 with consistency >0.95. Proposes a Minimum Viable Validation Protocol → apply to `review`/`hiring_manager` before they gate. |
| **Can We Trust LLM Judges** — Zhang et al., 2609.12002 (Sep 2026) | Capable examinees get lenient scores. Label-free weighted-majority-vote ensemble within 0.5 pt of oracle → cheap upgrade for `review`. |
| **Judging the Judges (position bias)** — 2406.07791 | Baseline for pairwise judge bias; relevant if you ever compare two drafts. |
| **Measuring Validity in LLM-based Resume Screening** — Castleman et al., 2602.18550 (Feb 2026) | Screeners don't reliably pick the more qualified candidate and don't abstain when equal. Argues for **deterministic** screening where possible — supports making `recruiter_sim` rule-based. |
| **Competence-Preserving Resume Perturbations** — Chen & Xiao, 2609.16517 (Sep 2026) | 30–41% pairwise decision reversals from reformatting alone. Your rewrite *is* a perturbation → add the invariance test (§4.4). |
| **Synapse** — Erol et al., 2604.02539 (Apr 2026) | Job-person fit: FAISS retrieval + contrastive rerank + LLM-guided evolutionary resume optimisation (+22% nDCG@10). Note: "numeric scoring per resume-job pair was unsuccessful even with rubrics" — matches your finding; pairwise/rank framing works better than 1–10 scores. |
| **Human and LLM-Based Resume Matching** — Vaishampayan et al., NAACL 2025 | GPT-4 lenient on skills, strict on certs; 5 disagreement sources incl. *unjustified inferences from job history* — exactly what `validate_facts` check 3 blocks. Validates that design. |

PDFs: `docs/papers/fetch_papers.sh` (cloud sandbox can't reach arxiv.org; run the script locally once).

---

## 6. Priority order

1. **Dedup + per-job budget claim** (2.1, 2.2) — stops money leaks before any polling exists. ~1 day.
2. **Checkpointer always on + `interrupt()` migration + `durability="sync"`** (2.3). ~1 day.
3. **Disable hot-path rerank; cache `Profile.load`** (2.5, 2.4). ~1 hour.
4. **Fix ATS `literal keywords` to JD-terms-in-PDF** (3.4) — the score currently can't distinguish drafts. ~half day.
5. **Native structured outputs + 1h cache TTL + Haiku cache warning** (3.2, 3.3). ~half day.
6. **Company ATS watcher (Personio first) + `companies.yaml` + scheduler** (4.1). ~2 days.
7. **Batches API path for the nightly queue** (4.2). ~1 day.
8. **`applications` table + judge κ logging + invariance test** (4.4). ~1 day.
9. **Deterministic `recruiter_sim`, ensemble `review`** (3.1). after 8 gives you data.
10. Repo README ingestion (§5.3) — or delete Chroma.

Portfolio note: items 1–5 and 8 are what a hiring manager for an AI Engineer role will ask about ("how do you know the judge is right?", "what happens on crash?", "what did it cost?"). Items 6–7 make it actually run unattended.

---

## Sources
- LangGraph interrupts: https://docs.langchain.com/oss/python/langgraph/interrupts
- LangGraph durability modes: https://reference.langchain.com/python/langgraph/types/Durability ; https://vadim.blog/durable-execution-agents-that-survive-failure-and-resume-where-they-left-off ; https://www.zenml.io/blog/langgraph-durable-runtime
- Anthropic prompt caching (TTL, min tokens, breakpoints): https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- Anthropic structured outputs: https://platform.claude.com/docs/en/build-with-claude/structured-outputs
- langchain-anthropic `with_structured_output`: https://reference.langchain.com/python/langchain-anthropic/chat_models/ChatAnthropic/with_structured_output
- Message Batches API (50%, caching stacks): https://www.respan.ai/articles/anthropic-message-batches-api
- ATS public APIs: https://cavuno.com/blog/ats-platforms-public-job-posting-apis
- MAST: https://arxiv.org/abs/2503.13657 ; https://github.com/multi-agent-systems-failure-taxonomy/MAST
- Judge reliability: https://arxiv.org/abs/2606.19544 ; https://arxiv.org/abs/2609.12002 ; https://arxiv.org/abs/2406.07791
- Resume screening validity: https://arxiv.org/abs/2602.18550 ; https://arxiv.org/abs/2609.16517 ; https://aclanthology.org/2025.findings-naacl.270.pdf
- Synapse: https://arxiv.org/abs/2604.02539
