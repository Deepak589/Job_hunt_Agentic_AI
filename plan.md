# JobPilot — Multi-Agent Job Search & CV Tailoring System

**Target:** Data Science / AI / ML Engineer + Werkstudent roles, Germany, English-speaking.
**Owner:** Deepak
**Status:** design v2

---

## 0. Design principles

1. **LLMs propose, code decides.** Every gate that determines *skip / apply / reject* is
   deterministic Python. The LLM produces structured data; it never makes the final call.
2. **Nothing is invented.** All CV content derives from `master_profile.yaml` + GitHub repo
   docs. A fact-validator node enforces this mechanically, not by prompt instruction.
3. **Human in the loop before any external action.** The graph interrupts before submission.
   No auto-apply, ever.
4. **Cheap models filter, expensive models write.** Anthropic-only stack: embeddings run
   locally, Haiku handles the funnel, Sonnet/Opus handle diagnosis and rewriting only for
   jobs that survive it. One provider, one SDK, one failure mode.
5. **Everything resumable.** Checkpointed state means a crash at node 7 of 40 jobs does not
   restart the run or re-spend tokens.

---

## 1. Architecture

LangGraph state machine with a checkpointer. Two graphs:

- **`ingest_graph`** — scheduled, batch. Fetch → normalize → dedupe → prefilter → persist.
- **`tailor_graph`** — per-job, invoked for jobs that pass prefilter. Contains the 5-role
  subgraph, the fact validator, the human interrupt, and rendering.

Separating them matters: ingest is IO-bound and idempotent; tailoring is LLM-bound, expensive,
and needs per-job checkpoint isolation (`thread_id = job.id`).

### Node graph

```
INGEST GRAPH  (cron, daily)
  fetch_sources ──> normalize ──> dedupe(content_hash) ──> prefilter ──> persist_jobs
                                                              │
                                                    (drop: wrong country /
                                                     no English / not Werkstudent /
                                                     already applied)

TAILOR GRAPH  (per job, thread_id = job.id)
  load_job
    ↓
  extract_requirements        # LLM → Requirement[]  (hard | soft | disqualifier)
    ↓
  retrieve_evidence           # per requirement, RAG over evidence_store
    ↓
  score_coverage              # deterministic: hard/soft coverage %
    ↓
  ┌─ hard_gap_gate ──────── blocking gap ────> log_skip ──> END
  │        │ no blocking gap
  │        ↓
  │  diagnose                 # LLM: gaps, matches, positioning mismatch
  │        ↓
  │  rewrite  <───────────────────────┐      # LLM: XYZ bullets + section order
  │        ↓                          │
  │  validate_facts ──── fail ────────┤      # DETERMINISTIC. no LLM.
  │        ↓ pass                     │
  │  review ──────────── score < 7 ───┤      # LLM judge
  │        ↓ pass                     │
  │        └── attempt_count >= 2 ────┘ → ship best + flag weakness
  │        ↓
  │  recruiter_sim            # LLM: keyword-literal pass/soft-fail/hard-fail
  │        ↓
  │  hiring_manager           # LLM: fit + "defensible in interview?" → verdict
  │        ↓
  │  INTERRUPT(human_approval)         # LangGraph interrupt — needs checkpointer
  │        ↓ approved
  │  render_documents         # Typst → two-column CV PDF + cover letter PDF
  │        ↓
  │  log_application ──> END
  └─
```

---

## 2. State schema

Single typed state object threaded through the graph. Without this, node 3 onward becomes
untraceable.

```python
# src/agentic_ai/state.py
from typing import Literal, Annotated
from pydantic import BaseModel, Field
import operator

ReqType = Literal["hard", "soft", "disqualifier"]

class Requirement(BaseModel):
    text: str
    type: ReqType
    keywords: list[str]
    evidence: list["Evidence"] = []      # filled by retrieve_evidence
    covered: bool = False                # filled by score_coverage

class Evidence(BaseModel):
    source: Literal["cv_bullet", "project", "repo_doc", "education"]
    source_id: str                       # e.g. "exp.acme.b3" or "repo:rag-pipeline"
    text: str
    similarity: float

class Job(BaseModel):
    id: str                              # content_hash
    source: str                          # adzuna | arbeitnow | usajobs | manual
    url: str
    title: str
    company: str
    location: str
    posted_at: str | None
    jd_text: str
    lang: Literal["en", "de", "mixed"]
    employment_type: str | None          # werkstudent | fulltime | intern | unknown

class Diagnosis(BaseModel):
    hard_gaps: list[str]
    soft_gaps: list[str]
    disqualifiers: list[str]
    matches: list[str]
    positioning_mismatch: str | None

class Draft(BaseModel):
    profile_line: str
    section_order: list[str]
    bullets: dict[str, list[str]]        # section_id -> bullets
    cover_letter: str
    highlighted_projects: list[str]

class Scores(BaseModel):
    hard_coverage: float                 # 0..1  deterministic
    soft_coverage: float                 # 0..1  deterministic
    semantic_fit: float                  # 0..1  embedding
    review_score: int | None             # 1..10 LLM judge
    recruiter: Literal["pass","soft_fail","hard_fail"] | None
    verdict: Literal["apply","fix_then_apply","skip"] | None

class JobState(BaseModel):
    job: Job
    requirements: list[Requirement] = []
    diagnosis: Diagnosis | None = None
    draft: Draft | None = None
    scores: Scores = Scores(hard_coverage=0, soft_coverage=0, semantic_fit=0)
    attempt_count: int = 0
    validation_errors: list[str] = []
    notes: Annotated[list[str], operator.add] = []   # append-only trace
    artifacts: dict[str, str] = {}                   # cv_pdf, cover_pdf paths
```

**Note the `Annotated[..., operator.add]` on `notes`** — LangGraph needs an explicit reducer
for any field multiple nodes append to, otherwise concurrent writes silently overwrite.

---

## 3. Node contracts

| Node | Input | Output (state delta) | Model | Deterministic |
|------|-------|---------------------|-------|---------------|
| `fetch_sources` | date window | `Job[]` raw | — | yes |
| `normalize` | raw payloads | `Job` | — | yes |
| `dedupe` | `Job` | drop / keep | — | yes |
| `prefilter` | `Job` | drop / keep + reason | — | yes |
| `extract_requirements` | `job.jd_text` | `requirements[]` | Haiku | no |
| `retrieve_evidence` | `requirements[]` | `requirement.evidence[]` | local embeddings | yes |
| `score_coverage` | `requirements[]` | `scores.hard/soft_coverage` | — | **yes** |
| `hard_gap_gate` | `requirements[]` | route | — | **yes** |
| `diagnose` | job + requirements + evidence | `diagnosis` | Claude | no |
| `rewrite` | diagnosis + master profile | `draft` | Claude | no |
| `validate_facts` | draft vs master profile | `validation_errors[]` | — | **yes** |
| `review` | draft + jd | `scores.review_score` | Claude | no |
| `recruiter_sim` | draft + hard reqs | `scores.recruiter` | Haiku | no |
| `hiring_manager` | everything | `scores.verdict` | Claude | no |
| `render_documents` | draft | `artifacts` | Typst | yes |
| `log_application` | state | SQLite row | — | yes |

---

## 4. Data sources

**Base (stable, ToS-clean):**
- **Adzuna API** — free tier, strong DE coverage, has `country=de`. Primary source.
  `https://developer.adzuna.com/`
- **Arbeitnow API** — DE/EU, no auth, remote-friendly, tags include `englishspeaking`.
  Highest signal-to-noise for your exact filter. `https://arbeitnow.com/api/job-board-api`
- **Jobs in Berlin / berlinstartupjobs** — RSS/JSON, English-first by default.

**Secondary (use, but don't depend on):**
- JSearch (RapidAPI) — largely Google-for-Jobs derived. Grey-ish, rate-limited, schema drifts.
  Wrap behind the same `JobSource` interface so it can be dropped without touching the graph.

**Explicitly excluded:** LinkedIn scraping. ToS violation, account-ban risk, and a hard
dependency you cannot run unattended.

**Manual injection path (build this — you will use it daily):**
A `jobpilot add <url>` / `jobpilot add --file jd.txt` CLI that pushes a single JD straight into
`tailor_graph`. Most of your real applications will come from jobs you found yourself. The
automated ingest is the supplement, not the main event. Extraction spec in §4.2.

### 4.1 Company career-page watcher

Aggregators under-index exactly your target: German startups and Mittelstand often never post
to Adzuna, and Werkstudent roles in particular frequently exist only on the company's own site.
Those roles also fill fast, so being a day early has real value.

**Do not scrape.** Most "careers pages" are a frontend over an ATS with a free, public,
structured board API:

| Vendor | Endpoint shape | Auth |
|---|---|---|
| Greenhouse | `boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` | none |
| Lever | `api.lever.co/v0/postings/{company}?mode=json` | none |
| Personio | `{company}.jobs.personio.de/xml` | none |
| Ashby | public board GraphQL endpoint | none |
| Join.com | public board JSON | none |

Personio matters most here — it is ubiquitous in German SMEs. HTML extraction is the fallback
for genuinely custom pages only.

```yaml
# config/companies.yaml   — 20-30 entries; beyond that curation stops being meaningful
- name: Zalando
  ats: greenhouse
  token: zalando
- name: Example Berlin GmbH
  ats: personio
  token: exampleberlin
- name: Custom Co
  ats: html
  url: https://customco.de/karriere
  poll_days: 7          # slow movers don't need daily
```

**Mechanics:**
1. Daily, fetch each board → current posting IDs
2. Diff against `company_snapshots` in SQLite
3. New IDs → fetch full JD → same `normalize → dedupe → prefilter` path as any other source
4. Unchanged → no work, no tokens
5. Board fetch fails 3 runs straight → notify (§20.3); a silently dead watcher is the worst
   failure mode, because absence of jobs looks identical to absence of postings

```sql
CREATE TABLE company_snapshots (
  company TEXT, ats TEXT, posting_ids TEXT,   -- JSON array
  fetched_at TEXT, consecutive_failures INT DEFAULT 0,
  PRIMARY KEY (company)
);
```

Architecturally free: it implements the same `JobSource` interface. One class per ATS vendor,
no graph changes.

### 4.2 URL → JD extraction

The path you will use most — you find a posting yourself and paste the link.

```
jobpilot add https://...        → fetch → extract → normalize → tailor_graph
jobpilot add --file jd.txt      → skip fetch
jobpilot add --stdin            → paste raw text
```

**Extraction order — try cheapest first, stop at first success:**

1. **Known ATS URL?** (`boards.greenhouse.io/...`, `jobs.lever.co/...`, `*.jobs.personio.de/...`)
   → hit that vendor's JSON API for the posting id. Structured, exact, free. Covers a large
   share of real links.
2. **JSON-LD `JobPosting`** in the page `<head>` — schema.org markup, present on most job pages
   because it feeds Google for Jobs. Gives `title`, `hiringOrganization`, `datePosted`,
   `employmentType`, `description` as clean fields.
3. **Readability extraction** (`trafilatura`) on the raw HTML → main content text.
4. **Manual fallback** — JS-rendered page or paywall: print a clear message and accept
   `--file` / `--stdin`. Do not add a headless browser to the ingest path for this. It is a rare
   case, and a browser dependency in a cron job is a maintenance burden out of all proportion
   to the benefit.

Always store the raw fetched HTML/JSON alongside the extracted text. When a diagnosis looks
wrong, the first question is whether extraction mangled the JD, and you cannot answer that
without the original.

**Language detection** runs here, not later — `lang` drives the prefilter (§17), and a
German-only posting should be dropped before it costs an LLM call.

### Source interface

```python
class JobSource(Protocol):
    name: str
    def fetch(self, since: datetime, queries: list[str]) -> list[dict]: ...
    def normalize(self, raw: dict) -> Job: ...
```

Adding a source = one class. No graph changes.

---

## 5. Evidence store — the thing that makes this better than prompting

This is the core upgrade over "embed CV, embed JD, cosine similarity". A blended similarity
score is a mushy signal: it rates "both documents are about tech" highly and tells you nothing
actionable. Instead, decompose.

### 5.1 Master profile (single source of truth)

`data/master_profile.yaml` — hand-maintained, never LLM-written:

```yaml
identity:
  name: Deepak Katukuri
  location: Berlin, Germany
  work_status: student (Werkstudent eligible, max 20h/week during term)
  languages: [English (fluent), ...]

experience:
  - id: exp.acme
    title: Data Analyst Intern
    company: Acme GmbH
    start: 2024-06
    end: 2024-12
    stack: [Python, SQL, Power BI]
    bullets:
      - id: exp.acme.b1
        outcome: "cut manual reporting time"
        metric: "8 hrs/week"           # ← facts live here, atomized
        method: "automated KPI dashboards in Power BI over live SQL"
        tags: [sql, powerbi, automation, reporting]

projects:
  - id: proj.rag
    name: Multi-agent RAG pipeline
    repo: github.com/Deepak589/...
    stack: [LangGraph, Chroma, Anthropic]
    metric: "reduced retrieval latency 40%"
    tags: [rag, langgraph, vectordb]

education: [...]
skills:
  expert:     [Python, SQL, pandas]
  proficient: [LangChain, scikit-learn, Power BI]
  familiar:   [Docker, AWS]
```

**Why atomized (`outcome` / `metric` / `method` as separate fields) rather than a finished
sentence:** the rewriter recombines them per JD, and the fact validator can assert every
number in the output exists in this file. A pre-written sentence gives you neither.

### 5.2 Evidence chunks

Index into Chroma at build time:
- every bullet (as `outcome + metric + method`)
- every project (name + stack + README summary)
- every skill with its supporting bullet ids
- GitHub repo `README.md` + `docs/*.md`, chunked (`~400 tokens`, `50` overlap)

**Embedding model:** `BAAI/bge-m3` via `sentence-transformers`.
Local, free, and **multilingual** — critical, because German JDs will appear even when
searching English-speaking roles, and an English-only embedder scores them near-random.
The stack is Anthropic-only for generation; do not add an OpenAI dependency for embeddings.

### 5.3 GitHub ingestion

Nightly: for each repo in `config/repos.yaml`, fetch README + `docs/*.md` via the GitHub API,
store raw in `data/repos/`, chunk into the evidence store. Cache by commit SHA — re-embed only
what changed.

---

## 6. Scoring — deterministic, per-requirement

```python
def score_coverage(reqs: list[Requirement]) -> Scores:
    for r in reqs:
        best = max((e.similarity for e in r.evidence), default=0.0)
        kw_hit = any(k.lower() in EVIDENCE_KEYWORD_SET for k in r.keywords)
        # both signals: semantic covers paraphrase, keyword covers ATS-literal match
        r.covered = best >= SEM_THRESHOLD or kw_hit

    hard = [r for r in reqs if r.type == "hard"]
    soft = [r for r in reqs if r.type == "soft"]
    return Scores(
        hard_coverage = mean(r.covered for r in hard) if hard else 1.0,
        soft_coverage = mean(r.covered for r in soft) if soft else 1.0,
        ...
    )
```

### The hard-gap gate

```python
def hard_gap_gate(state: JobState) -> Literal["skip", "diagnose"]:
    if state.job_matches_disqualifier():          # e.g. "requires C1 German"
        return "skip"
    blocking = [r for r in state.requirements if r.type == "hard" and not r.covered]
    if len(blocking) > MAX_BLOCKING_HARD_GAPS:    # start at 1
        return "skip"
    return "diagnose"
```

This is a rule, not a threshold. A JD demanding 3 years of Rust with zero Rust evidence is not
a "low score" — it is a categorically different outcome, and no amount of rewriting fixes it.
Encoding that as `score < 0.6` loses the distinction and wastes Claude calls on jobs you
cannot get.

### ATS score (target ≥ 95, floor 90)

No employer ATS emits a score, so this one is **ours and computed**, not a vendor number and
not an LLM's opinion. `CLAUDE.md` states the requirement; this section is the canonical rubric,
implemented in `src/agentic_ai/scoring/ats.py` — deterministic, unit-tested.

**Blocking gates** — any one fails → `total = 0`, verdict `skip` or `fix_then_apply`. Not a
deduction: fabrication cannot be offset by scoring well elsewhere.

| Gate | Must be | Source |
|---|---|---|
| Numbers not traceable to `master_profile.yaml` / repo docs | 0 | §8 validator |
| Tech claimed with no evidence chunk | 0 | §8 validator |
| Employers / titles / dates not in real history | 0 | §8 validator |
| JD disqualifier present (e.g. "requires C1 German") | 0 | `requirements[type=disqualifier]` |

**Points — 100 total**

| # | Component | Pts | Formula |
|---|---|---|---|
| 1 | Hard requirement coverage | 50 | `50 × covered(hard) ÷ count(hard)` — from `score_coverage()` above |
| 2 | Literal keyword match | 20 | `20 × verbatim_hits ÷ evidenced_jd_terms` |
| 3 | PDF parseability | 15 | `15 × fields_recovered ÷ fields_expected` — name, email, phone, location, every section heading, every skill |
| 4 | Quantification (X-Y-Z) | 10 | `10 × bullets_with_metric ÷ bullets_metric_available` |
| 5 | Positioning match | 5 | section order matches JD role type (§7) AND profile line targets it → 5, else 0 |

**Thresholds**

| Total | Verdict |
|---|---|
| ≥ 95 | `apply` |
| 90–94 | `fix_then_apply` — name the exact component losing points |
| < 90 | **`skip`** — state what would have to become true to reach 90 |
| any gate failed | `skip` / `fix_then_apply` regardless of points |

```python
def ats_score(state: JobState, pdf_fields: ParsedPDF) -> AtsScore:
    if gates_failed(state):                       # fabrication or disqualifier
        return AtsScore(total=0.0, gates=..., verdict="skip")
    pts = (
        50 * frac(covered(hard_reqs), hard_reqs)          # §6 coverage
      + 20 * frac(verbatim_hits, evidenced_jd_terms)      # denominator = evidenced only
      + 15 * frac(pdf_fields.recovered, pdf_fields.expected)
      + 10 * frac(bullets_with_metric, bullets_metric_available)
      +  5 * (section_order_matches(state) and profile_line_targets(state))
    )
    return AtsScore(total=pts, verdict=verdict_for(pts))  # >=95 apply, 90-94 fix, <90 skip
```

Two properties keep it honest:

- **Keyword denominator is `evidenced_jd_terms`, not all JD terms.** A JD term with no
  evidence must stay off the CV; its absence costs nothing. Without this the score's gradient
  is "add more JD words", which is keyword stuffing and degrades the human read.
- **Fabrication is a gate, not a deduction** — §8's validator zeroes the score outright. No
  amount of coverage offsets a number that isn't real.

`hard_coverage == 1.0` remains a **floor** independent of the total: component 1 is 50 points,
so an uncovered hard requirement caps the score below 95 by construction, and `hard_gap_gate`
skips the job before it ever reaches scoring.

Component 3 requires the parseability check from §10 — re-extract text from the rendered PDF
and assert every field survives. That is the failure mode that silently loses interviews, and
it is the only part of "passing ATS" that maps to real employer software.

**Report format** — emitted by `jobpilot review` and the §20.2 review Artifact. Counts first,
arithmetic second; a total with no fact table under it is not a score:

```
ATS SCORE: 97.2 / 100          verdict: apply

GATES
  unverified numbers ........ 0 ✓
  unevidenced tech .......... 0 ✓
  fabricated history ........ 0 ✓
  JD disqualifiers .......... 0 ✓

POINTS
  hard req coverage ......  7/7   → 50.0
  literal keywords ....... 11/12  → 18.3
  pdf parseability ....... 14/14  → 15.0
  quantification .........  8/9   →  8.9
  positioning ............ match  →  5.0
                                   ─────
                                    97.2

UNCOVERED HARD REQS: none
DROPPED JD TERMS (no evidence, correctly absent): PyTorch, Kubernetes
```

---

## 7. The 5-role subgraph (from CLAUDE.md, as code)

Each role becomes a node with a Pydantic output schema. The prompts live in
`prompts/*.md` — versioned, diffable, testable — not inline strings.

1. **`diagnose`** → `Diagnosis`. Runs after the gate. Line-by-line CV vs JD.
   Receives the retrieved evidence, so it cites `source_id` rather than guessing.
2. **`rewrite`** → `Draft`. XYZ formula enforced by schema:
   each bullet is `{outcome, metric|null, method, source_bullet_id}`.
   The `source_bullet_id` requirement is what makes step 3 possible.
3. **`validate_facts`** → deterministic (see §8).
4. **`review`** → `{score:int, weaknesses:list[str]}`. Loops to `rewrite`, max 2.
5. **`recruiter_sim`** → `{result, reason}`. Fast, keyword-literal, cheap model on purpose —
   simulating a shallow filter with a deep model defeats the point.
6. **`hiring_manager`** → `{verdict, why, indefensible_bullets:list[str]}`.
   The "could you defend this in an interview" check is the highest-value one — keep it on
   Claude.

### Section ordering (deterministic, from CLAUDE.md)

```python
SECTION_ORDER = {
    "data_scientist": ["skills", "projects", "experience"],
    "data_analyst":   ["experience", "skills", "projects"],
    "ai_engineer":    ["projects", "experience", "skills"],
    "fullstack_ship": ["profile", "projects", "experience", "skills"],
}
```
Role classification is a small classifier node (cheap model + enum output), then a dict lookup.
Do not let the LLM freestyle the section order — it will be inconsistent across applications.

---

## 8. Fact validator — highest-value guardrail

Deterministic. No LLM. This is what separates a toy from something you'd send to employers.

```python
def validate_facts(draft: Draft, master: MasterProfile) -> list[str]:
    errors = []
    allowed_numbers = master.all_metrics()      # {"8 hrs/week", "40%", "2M", ...}
    allowed_tech    = master.all_tech()          # normalized, lowercased
    allowed_titles  = master.all_titles()
    allowed_orgs    = master.all_orgs()

    for section, bullets in draft.bullets.items():
        for b in bullets:
            if b.source_bullet_id not in master.bullet_ids:
                errors.append(f"{section}: bullet cites unknown source {b.source_bullet_id}")
            for num in extract_numerics(b.text):          # regex: %, x, hrs, k, M, years
                if not matches_any(num, allowed_numbers):
                    errors.append(f"{section}: unverified number '{num}' in: {b.text[:60]}")
            for tech in extract_tech_entities(b.text):    # gazetteer match
                if tech.lower() not in allowed_tech:
                    errors.append(f"{section}: claims unevidenced tech '{tech}'")
    return errors
```

Any error routes back to `rewrite` with the offending tokens named in the retry prompt.
**Unit test this node hard** — it is the one component whose failure sends a lie to an employer.

Build a `tests/test_fact_validator.py` with adversarial cases: inflated percentages, a tech
name that appears in the JD but not your profile (the most likely hallucination — the model
sees "Kubernetes" in the JD and wants to please), a fabricated employer.

---

## 9. Human-in-the-loop

```python
from langgraph.checkpoint.sqlite import SqliteSaver

graph = builder.compile(
    checkpointer=SqliteSaver.from_conn_string("data/checkpoints.db"),
    interrupt_before=["render_documents"],
)
```

**`interrupt` does not work without a checkpointer** — this was missing from v1 and would have
failed at runtime. State must be persisted for the graph to pause, exit the process, and
resume later from your approval.

Approval surface, in build order:
1. **v1:** CLI — `jobpilot review` prints diagnosis + verdict + draft, waits for `y/n/edit`.
2. **v2:** a local Streamlit/FastAPI page listing pending jobs with a diff view of
   master bullets → tailored bullets. Much faster to triage 15 jobs.

Resume: `graph.invoke(None, config={"configurable": {"thread_id": job_id}})`.

---

## 10. Document rendering

**Use Typst, not `docxtpl`.**

`docxtpl` for a two-column CV means fighting Word's table/section model, and the output drifts
between runs. Typst gives:
- native two-column layout in ~10 lines
- deterministic PDF output, byte-stable for identical input
- template versioned in git, diffable
- single binary, fast, no LibreOffice dependency

```
templates/
  cv_two_column.typ        # takes a JSON data file
  cover_letter.typ
```

```bash
typst compile --input data=build/job_123.json templates/cv_two_column.typ out/cv.pdf
```

Fallback if a docx is specifically requested: render Typst → PDF as the primary artifact, and
keep a separate `docx` path via the docx skill for the rare portal that demands `.docx`. Do not
make docx the primary — the PDF is what you send 95% of the time.

---

## 11. Persistence

SQLite (`data/jobpilot.db`). Correct at this scale — do not reach for Postgres.

```sql
CREATE TABLE jobs (
  id TEXT PRIMARY KEY,              -- sha256(title|company|normalized_jd)
  source TEXT, url TEXT, title TEXT, company TEXT, location TEXT,
  posted_at TEXT, jd_text TEXT, lang TEXT, employment_type TEXT,
  first_seen TEXT, last_seen TEXT
);

CREATE TABLE runs (
  run_id TEXT PRIMARY KEY, job_id TEXT REFERENCES jobs(id),
  started_at TEXT, finished_at TEXT,
  hard_coverage REAL, soft_coverage REAL, semantic_fit REAL,
  review_score INT, recruiter TEXT, verdict TEXT,
  attempt_count INT, skipped_reason TEXT,
  tokens_in INT, tokens_out INT, cost_usd REAL
);

CREATE TABLE requirements (
  run_id TEXT, idx INT, text TEXT, type TEXT,
  covered INT, best_similarity REAL, evidence_ids TEXT
);

CREATE TABLE applications (
  job_id TEXT PRIMARY KEY, applied_at TEXT,
  cv_path TEXT, cover_path TEXT,
  outcome TEXT,                    -- pending|rejected|screen|interview|offer
  outcome_at TEXT, notes TEXT
);
```

`id` is a **content hash, not the URL** — the same job gets reposted weekly under new URLs, and
URL-based dedupe will have you re-tailoring the same posting every run.

---

## 12. Observability & cost control

Non-negotiable for something that spends money unattended.

- **Tracing:** LangSmith or Langfuse, enabled from day 1. When a verdict looks wrong you need
  the exact prompt/response of the node that produced it, not a guess.
- **Cost ledger:** write `tokens_in / tokens_out / cost_usd` per run into `runs`.
  A `jobpilot cost --last 7d` command tells you which node is expensive. It will be `rewrite`.
- **Structured logs:** JSON lines with `run_id`, `job_id`, `node`, `duration_ms`.
- **Budget guard:** a hard `MAX_DAILY_COST_USD` in config; ingest refuses to run past it.
  Cheap insurance against a retry loop burning a weekend.

### Model routing

| Purpose | Model | Why |
|---|---|---|
| Embeddings | `bge-m3` local | free, multilingual, offline |
| `extract_requirements`, `recruiter_sim`, role classifier | Claude Haiku | high volume, shallow, latency-sensitive |
| `diagnose`, `rewrite`, `review`, `hiring_manager` | Claude Sonnet/Opus | judgment, tone, defensibility |

Route via a single `get_model(purpose: str)` factory, config-driven. Never hardcode a model
name in a node — changing a tier should be a YAML edit.

```yaml
# config/models.yaml
extract_requirements: haiku
role_classifier:      haiku
recruiter_sim:        haiku
diagnose:             sonnet
rewrite:              sonnet
review:               sonnet
hiring_manager:       sonnet     # promote to opus if verdicts prove unreliable
```

**Single provider (Anthropic) by choice.** A second provider buys marginal cost savings on the
shallow nodes and costs you: a second SDK, a second auth path, a second rate-limit regime, a
second set of structured-output quirks, and prompts that silently behave differently across
families. Not worth it at this volume.

**The tiering still matters though.** Running Opus on `extract_requirements` would be roughly an
order of magnitude more expensive for a task where Haiku is not measurably worse — it is
extraction against an explicit schema, not judgment. Keep the Haiku/Sonnet split; it is the
same architecture, just inside one provider.

---

## 13. Reliability

- **Retries:** `tenacity` with exponential backoff + jitter on every external call
  (job APIs, GitHub, LLM providers). Distinguish retryable (429, 5xx, timeout) from fatal
  (401, 400) — retrying a bad API key 5 times just delays the error.
- **Circuit breaker per source:** 3 consecutive failures → mark source down, continue with the
  rest. One dead API must not kill the daily run.
- **Idempotency:** every node keyed on `(job_id, node_name)`; re-running a completed node
  returns the checkpointed result.
- **Partial failure:** ingest processes jobs independently; one malformed JD is logged and
  skipped, never fatal.
- **Timeouts:** hard ceiling per node (`rewrite` 90s, others 30s), and a per-job total.

---

## 14. Evaluation

Without this you will change a prompt, silently regress diagnosis quality, and not notice for
two weeks.

**Golden set:** `evals/golden/*.yaml`, 20 JDs you have hand-labelled:
```yaml
job_id: golden_007
jd_file: jds/007_ml_engineer_zalando.txt
expected:
  hard_requirements: ["PyTorch", "AWS SageMaker", "German B2"]
  disqualifiers: ["German B2"]
  verdict: skip
  reason: "German B2 required"
```

**Metrics:**
| Metric | Target | Measures |
|---|---|---|
| Requirement extraction F1 | > 0.85 | does it find the real hard reqs |
| Disqualifier recall | **1.00** | never miss an auto-reject — asymmetric cost |
| Verdict agreement | > 0.80 | matches your own judgement |
| Fact-validator false-negative rate | **0.00** | never lets a hallucination through |
| Cost per tailored application | < $0.15 | economics |

**Golden-set fact tests:** hand-write drafts containing known hallucinations and assert the
validator catches every one. Disqualifier recall and validator FN rate are the two that must be
perfect — the others are quality dials.

Run on every prompt change: `pytest evals/ -m golden`.

---

## 15. Repo layout

```
src/agentic_ai/
  state.py                 # JobState, Job, Requirement, Draft, Scores
  config.py                # pydantic-settings, .env + config.yaml
  models.py                # get_model(purpose) factory
  sources/                 # adzuna.py arbeitnow.py jsearch.py base.py
  evidence/
    profile.py             # master_profile.yaml loader + validation
    github.py              # repo doc ingestion
    store.py               # chroma index / query
  graphs/
    ingest.py
    tailor.py
    nodes/                 # one file per node
  validators/
    facts.py               # THE guardrail
    schema.py
  render/
    typst.py
  db/
    schema.sql  repo.py
  cli.py                   # add | run | review | cost | status
prompts/                   # versioned .md prompts
templates/                 # .typ
data/                      # master_profile.yaml, *.db, chroma/
evals/                     # golden set + harness
tests/
```

---

## 16. Build order

Each phase ends with something usable. Do not build ingest first — you cannot test tailoring
without jobs, but you can paste a JD.

**Phase 1 — the core loop, manual input (highest value, ~1 week)**
- `master_profile.yaml` + loader + validation
- evidence store (chroma + bge-m3), CV bullets only
- `extract_requirements` → `retrieve_evidence` → `score_coverage` → `hard_gap_gate`
- CLI: `jobpilot add --file jd.txt` prints the gap report
- URL extraction (§4.2) — ATS API → JSON-LD → readability → manual fallback
- *Ship point:* this alone beats manual JD reading.

**Phase 2 — tailoring + the guardrail**
- `diagnose` → `rewrite` → `validate_facts` → `review` loop
- `validate_facts` unit tests first, before the rewriter is trusted
- Typst templates, PDF output
- `ats_score` + PDF parseability check (§6) — re-extract text from the rendered PDF, assert
  name/email/phone/headings/skills all survive. Component 3 of the score depends on it.

**Phase 3 — full 5-role graph + human loop**
- `recruiter_sim`, `hiring_manager`
- SqliteSaver + `interrupt_before` + `jobpilot review` CLI
- SQLite persistence, cost ledger

**Phase 4 — automation**
- Adzuna + Arbeitnow sources, dedupe, prefilter
- career-page watcher (§4.1) — Greenhouse/Lever/Personio board APIs
- GitHub repo ingestion
- **batch runner + limit guard (§19)** — build the limits with the runner, never after
- **Cowork control plane (§20)**: scheduled task, review Artifact, notifications
- Langfuse tracing

**Phase 5 — feedback loop**
- outcome tracking (`applications.outcome`)
- **procedural memory (§21.4)**: edit capture → rule proposals → your approval
- weekly rollup to Cowork project memory, incl. top recurring hard gaps

**Phase 6 — assisted apply (§22, optional)**
- ATS field maps for Workday / Greenhouse / Lever / Personio
- portal detector + Claude-in-Chrome fill step
- hard stop before attestations and submit — enforced in code, not prompt
- *Skippable. Nothing upstream depends on it.*
- weekly report: which roles/companies convert
- tune `SEM_THRESHOLD` and `MAX_BLOCKING_HARD_GAPS` against real outcomes
- retrospective: for interviews won, which requirements were covered? Feed into scoring weights.

---

## 17. Prefilter rules (from CLAUDE.md priority rules)

Deterministic, runs before any LLM call — this is where the money is saved.

```python
def prefilter(job: Job) -> tuple[bool, str | None]:
    if job.id in already_applied:            return False, "already_applied"
    if not in_germany(job.location):         return False, "outside_germany"
    if requires_german_fluency(job.jd_text): return False, "german_required"
    if job.lang == "de" and not has_english_signal(job.jd_text):
                                             return False, "german_only_posting"
    if TARGET_MODE == "werkstudent" and job.employment_type not in
       ("werkstudent","intern","part_time","unknown"):
                                             return False, "not_werkstudent"
    if is_senior_only(job.title):            return False, "seniority_mismatch"
    return True, None
```

Every drop is logged with its reason. Review the drop-reason histogram weekly — an
over-aggressive filter is invisible unless you measure it, and "german_required" firing on
80% of postings means your regex is wrong, not that Berlin has no English jobs.

---

## 18. Open decisions

1. **Interview-prep agent?** Once a verdict is `apply`, a node generating likely questions from
   the JD + your (now known) weak bullets is nearly free — the state already holds everything.
   Worth adding in Phase 5.
2. **Cover letter model.** Cheap models write generic cover letters. Keep on Claude.
3. **German-language output.** Currently English-only. If DE cover letters become needed, add a
   translate-and-review node rather than prompting for German directly.
4. **`MAX_BLOCKING_HARD_GAPS`.** Start at 1. Tune once you have 30 runs and real outcomes.

---
---

## 19. Batch runner — "the conductor"

Code, not an agent. There is no LLM supervisor in this system: every routing decision is
encodable (§6 gate, §7 loop counters, §17 prefilter), so putting a model in front of them buys
nondeterminism and token cost for nothing. The runner is the process that drives the batch.

### Responsibilities

- Pull pending jobs from SQLite, ordered by `semantic_fit` desc (best jobs get budget first)
- Fan out `tailor_graph` with bounded concurrency
- Enforce every limit in §19.2; stop cleanly, never mid-write
- Isolate failures: one bad job logs and skips, never kills the batch
- Emit a daily digest (§20)

```python
# src/agentic_ai/runner.py
async def run_batch(cfg: Limits) -> DigestReport:
    guard = LimitGuard(cfg, db)
    if not guard.can_start():
        return DigestReport(skipped_reason=guard.blocking_reason)

    queue = db.pending_jobs(order_by="semantic_fit", desc=True)
    sem = asyncio.Semaphore(cfg.concurrency)

    async def one(job):
        async with sem:
            if not guard.claim(job):            # re-check per job, not once up front
                return None
            try:
                return await tailor_graph.ainvoke(
                    {"job": job},
                    config={"configurable": {"thread_id": job.id}},
                )
            except Exception as e:
                db.log_failure(job.id, e); return None

    results = await asyncio.gather(*(one(j) for j in queue))
    return build_digest([r for r in results if r])
```

`guard.claim()` is re-checked **per job**, not once at the start — a batch of 40 must stop at
the limit, not blow through it because the check passed when the queue was empty.

### 19.2 Limits

```yaml
# config/limits.yaml
concurrency: 3                  # above ~3 you hit provider rate limits
max_tailored_per_day: 6         # full tailor runs (the expensive path)
max_pending_review: 10          # BACKPRESSURE — see below
max_daily_cost_usd: 2.00
max_per_company_days: 30        # one active application per company per 30d
max_rewrite_attempts: 2         # already in the graph; mirrored here for visibility
```

**Terminology:** the system does not apply. It *prepares* applications and stops at a human
checkpoint (§9). `max_tailored_per_day` caps how many tailored packages are produced, not how
many are submitted. Submission count is whatever you personally approve.

**`max_pending_review` is the limit that actually matters.** If 10 tailored packages are
already sitting unreviewed, generating 10 more is pure waste — you become the bottleneck, the
queue rots, and you've spent tokens on drafts you'll never read. The runner refuses to start
when `count(pending_approval) >= max_pending_review`. This is backpressure, and it is the
difference between a system that helps and a system that generates noise.

**On `max_tailored_per_day: 6`** — deliberately low. For job search, 6 genuinely tailored
applications outperform 50 generic ones by a wide margin, and 6/day is already ~30/week, which
is more than you can follow up on properly. Raise it only if review capacity proves higher.

**Per-company throttle:** applying to the same company twice in a month with two differently-
framed CVs is actively harmful — recruiters see both. `max_per_company_days` blocks it, and the
`diagnose` node is given the prior application (§21) so a legitimate re-apply is consistent
with what you said last time.

### 19.3 Limit guard

```python
class LimitGuard:
    def can_start(self) -> bool:
        if self.db.pending_review_count() >= cfg.max_pending_review: return False
        if self.db.cost_today() >= cfg.max_daily_cost_usd:           return False
        return True

    def claim(self, job) -> bool:
        if self.tailored_today >= cfg.max_tailored_per_day:          return False
        if self.db.cost_today() >= cfg.max_daily_cost_usd:           return False
        if self.db.applied_to_company_within(job.company, cfg.max_per_company_days):
            self.db.log_skip(job.id, "company_throttle");            return False
        self.tailored_today += 1
        return True
```

Every refusal is logged with its reason and appears in the digest. A limit that silently drops
work is worse than no limit — you need to see "stopped at 6/6, 14 still queued" to know whether
to raise it.

---

## 20. Cowork control plane — review, notes, notifications

The pipeline stays local, deterministic and testable. Cowork is the **human interface layer**
on top of it: scheduling, review surface, notifications, notes.

**Boundary rule — do not violate this:** no pipeline logic lives in Cowork. Each scheduled run
is a fresh, non-deterministic session; putting graph logic there makes it untestable and
unversioned. Cowork calls the CLI and renders the result. That is all.

```
  Cowork scheduled task (daily 08:00 CET)
        │  runs on the linked Mac
        ▼
  jobpilot run --digest json      ← the local pipeline, unchanged
        │
        ├──> publish review Artifact  (pending queue, diff view, approve/skip)
        ├──> push notification        ("3 ready to review, 1 hard-gap skip")
        └──> project memory           (weekly rollup, learned preferences)
```

### 20.1 Scheduled run

A Cowork scheduled task with `requires_local_device: true`, firing weekday mornings. Each run:

1. `jobpilot ingest` → fetch, dedupe, prefilter
2. `jobpilot run` → batch runner within limits
3. `jobpilot digest --json` → structured summary
4. Publish/update the review Artifact
5. Push notification if anything needs you

Cron lives in Cowork rather than launchd so the digest and notification are one step, and the
run history is visible without SSH-ing into your own laptop.

### 20.2 Review surface

Replaces the Streamlit idea in §9. A published Artifact, updated in place each run:

- One card per pending job: company, title, verdict, `hard_coverage`, recruiter result
- **Diff view** — master bullet → tailored bullet, side by side. This is the whole point;
  you're checking what changed, not re-reading your CV.
- Links to the rendered CV/cover PDFs
- Approve / skip / request-rewrite buttons, and a free-text note field
- Any `validation_errors` shown in red at the top — those must never be silently swallowed

It's a URL, so you triage from your phone. Approvals write back to SQLite; `jobpilot resume`
picks them up and continues each interrupted graph from its checkpoint.

### 20.3 Notifications

Push only when action is needed or something is wrong. Notification fatigue kills these systems.

| Event | Notify | Why |
|---|---|---|
| N packages ready for review | yes | the one that needs you |
| `validation_errors` non-empty | yes | possible hallucination, look now |
| Daily cost limit hit | yes | budget signal |
| Source down 3 runs straight | yes | silent ingest failure is the worst failure mode |
| Run completed, nothing pending | no | digest covers it |
| Job skipped on hard gap | no | expected, goes in the digest |

### 20.4 Notes and rollups

- **Per-job notes** — free text from the review Artifact → `applications.notes`. Where you
  record why you skipped, or what to mention in a follow-up.
- **Weekly rollup** → Cowork project memory: conversion by role type, which requirements keep
  showing as gaps, what you rewrote by hand most often. That last one is the input to §21.4.
- **Recurring gaps are a signal about you, not the CV.** If "Kubernetes" is a hard gap in 12
  JDs, the fix is a weekend project, not a better bullet. The rollup should surface the top 5
  recurring gaps explicitly.

---

## 21. Memory architecture

The common failure is one vector store that everything reads and writes. That produces
contamination (the rewriter "remembers" a hallucination from three jobs ago) and unbounded
context growth. Memory here is **typed by lifetime, and scoped per node.**

### 21.1 Five memory types

| Type | Contents | Store | Lifetime | Written by |
|---|---|---|---|---|
| **Working** | current `JobState` | LangGraph checkpointer | one job | graph nodes |
| **Semantic** | who you are: profile, projects, repo docs | `master_profile.yaml` + Chroma | months | **you**, by hand |
| **Episodic** | what happened: past runs, drafts, verdicts, prior applications per company | SQLite | forever | runner (deterministic) |
| **Procedural** | learned style rules from your edits | `data/preferences.yaml` | forever | **you approve each one** |
| **Outcome** | what actually converted | SQLite `applications.outcome` | forever | you, on the review surface |

### 21.2 Scoped access — which node sees what

Giving every node everything is the mistake. Each gets the minimum:

| Node | Working | Semantic | Episodic | Procedural | Outcome |
|---|---|---|---|---|---|
| `extract_requirements` | JD only | — | — | — | — |
| `retrieve_evidence` | reqs | ✅ full | — | — | — |
| `score_coverage` | reqs+evidence | — | — | — | — |
| `diagnose` | ✅ | ✅ | prior apps to this company | — | — |
| `rewrite` | ✅ | ✅ full | prior draft for this company | ✅ | — |
| `review` | ✅ | metrics only | — | ✅ | — |
| `recruiter_sim` | draft + hard reqs | — | — | — | — |
| `hiring_manager` | ✅ | ✅ | prior apps | — | aggregate stats |

Two deliberate choices:

- **`extract_requirements` is fully stateless** — it sees the JD and nothing else. Give it your
  profile and it starts finding the requirements it expects you to have. Contaminating the
  requirement extractor with your CV destroys the entire gap analysis downstream, because the
  gaps are defined by what the JD asks for, independent of you.
- **`recruiter_sim` is stateless too** — a real first-pass screen has no context. Feeding it
  memory makes it too generous and defeats the simulation.

### 21.3 Write policy

**LLM nodes never write to long-term memory.** Writes are either deterministic or
human-confirmed:

| Store | Write path |
|---|---|
| Working | graph nodes, automatic, dies with the job |
| Semantic | you edit the YAML; GitHub docs sync deterministically by commit SHA |
| Episodic | runner writes structured rows; no free text from a model |
| Procedural | proposed from your edits, **shown to you, persisted only on approval** |
| Outcome | you, from the review surface |

Rationale: an autonomous memory write is permanent and compounds. One bad inference —
"Deepak prefers understated phrasing" from a single edit where you were just fixing a typo —
becomes a rule that degrades every future draft, and you will never trace the regression back
to it. Memory poisoning is very hard to debug precisely because the poisoned entry looks like
a legitimate preference.

### 21.4 Procedural memory — learning from your edits

The one place the system genuinely gets better over time:

1. You edit a tailored bullet on the review surface
2. Runner diffs original → your version, stores the pair in `edits`
3. Weekly, a job clusters recent edits and proposes rules:
   *"You replaced 'Architected' with 'Built' 6 times — always prefer 'Built'?"*
4. You accept or reject each proposal
5. Accepted rules land in `preferences.yaml` and are injected into `rewrite` and `review`

```yaml
# data/preferences.yaml  — human-approved only
style:
  - rule: "prefer 'Built' over 'Architected'"
    evidence_count: 6
    approved: 2026-09-10
  - rule: "never open a cover letter with 'I am writing to express'"
    evidence_count: 4
    approved: 2026-09-10
content:
  - rule: "do not surface the university coursework project unless JD is academic"
    evidence_count: 3
    approved: 2026-09-10
```

Cap it — 25 rules max, evict lowest `evidence_count`. Unbounded preference lists silently
become a second, unversioned prompt that nobody reviews.

### 21.5 Context budget per node

Retrieval is bounded, not "stuff everything in":

| Node | Budget | Contents |
|---|---|---|
| `extract_requirements` | ~4k | JD only |
| `diagnose` | ~8k | reqs + top-3 evidence per req + prior app summary |
| `rewrite` | ~12k | diagnosis + relevant profile sections + preferences |
| `review` | ~6k | draft + hard reqs + preferences |
| `hiring_manager` | ~8k | draft + diagnosis + verdict history |

`rewrite` gets the **relevant** profile sections, not the whole file — retrieved by the same
per-requirement matching from §5. A profile that grows to 40 projects must not linearly inflate
every rewrite call.
---

## 22. Assisted apply (Phase 6 — optional, build last)

### What this is not

The system does not submit applications. Agents technically *can* — browser automation fills
forms and clicks buttons fine — but four reasons say don't:

- **Legal attestation.** Applications carry "I certify this information is true" and
  work-authorization declarations. That is a statement in your name. An agent ticking it means
  you attested to something you never read. This is the blocking reason; the rest are practical.
- **Irreversible, one shot.** Roughly one application per company per role. A bad auto-submitted
  package is a burned opportunity you never find out about.
- **ToS.** LinkedIn Easy Apply automation is explicitly prohibited and enforced. Indeed similar.
- **Friction.** CAPTCHA/Turnstile, auth walls, per-portal form schemas that break weekly.
  High maintenance, low value.

### What this is

The agent fills the form in **your** browser and stops. You read it, tick the attestations
yourself, click submit. You keep the legal act and the final review; you skip retyping your
address and education history into Workday for the ninth time.

```
render_documents → INTERRUPT(approve) → assisted_fill → HANDOFF → you submit → log
                                             │
                                    agent stops here, hard
```

### 22.1 Components

**Field mapper** — `master_profile.yaml` → ATS field names. Four vendors cover most German
employers: Workday, Greenhouse, Lever, Personio. One YAML per vendor:

```yaml
# config/ats/greenhouse.yaml
selectors:
  first_name:      "input#first_name"
  last_name:       "input#last_name"
  email:           "input#email"
  phone:           "input#phone"
  resume_upload:   "input[type=file][name*=resume]"
  cover_upload:    "input[type=file][name*=cover]"
  linkedin:        "input[id*=urls][id*=LinkedIn]"
never_touch:
  - "input[type=checkbox]"        # attestations, EEO, consent
  - "input[type=radio]"
  - "button[type=submit]"
```

**Portal detector** — identify vendor from URL/DOM signature, load the matching map. Unknown
vendor → fill nothing, open the page, tell you it's unmapped.

**Fill step** — Claude in Chrome (your session, your cookies), text fields + file uploads only.

**Hard stop** — `never_touch` is enforced in code before any action, not by prompt. The agent
cannot click submit even if it decides it should.

### 22.2 Non-negotiable rules

1. Never tick a checkbox or radio. Attestations, EEO/diversity questions, consent, work
   authorization — all yours.
2. Never click submit.
3. Never invent an answer to a free-text question. Unmapped or unanswerable field → leave empty
   and list it in the handoff summary.
4. Salary expectation is always left blank. It is a negotiation decision, not a data field.
5. Screenshot the filled form before handoff, store it against the run. If a portal misbehaves
   you need a record of what was entered.

### 22.3 Handoff output

```
Ready: Senior Data Analyst @ Zalando (Greenhouse)
Filled:   name, email, phone, LinkedIn, CV, cover letter
Left for you (4):
  ☐ Work authorization  — checkbox, requires your declaration
  ☐ Earliest start date — not in profile
  ☐ Salary expectation  — deliberate: yours to decide
  ☐ "Why Zalando?"      — free text, draft below to paste or rewrite
Then: review the whole form and submit.
```

### 22.4 Why this is last

Highest maintenance, lowest value in the system. Portal DOMs break constantly, so the vendor
maps need upkeep forever. It saves ~15 min per application; the tailoring pipeline (Phases 1–3)
saves hours and is what actually changes outcomes. Build this only once the core loop runs
daily and the retyping genuinely annoys you.

If you skip it permanently, nothing else in the plan is affected — the pipeline ends at
`render_documents` + human approval and remains complete.
---

## Appendix A — Dependencies

Anthropic-only for generation; embeddings run locally. One provider, one SDK, one failure mode.

### Core orchestration
| Package | Role |
|---|---|
| `langgraph` | state machine: nodes, conditional edges, `interrupt` |
| `langgraph-checkpoint-sqlite` | `SqliteSaver` — **`interrupt` does not work without it** |
| `langchain-core` | structured-output binding |
| `langchain-anthropic` | the only model provider |
| `pydantic` | `JobState` + every node output schema |
| `pydantic-settings` | config from `.env` + YAML |

### Retrieval
| Package | Role |
|---|---|
| `sentence-transformers` | runs `BAAI/bge-m3` locally — free, multilingual (§5.2) |
| `chromadb` | evidence store, persists to disk, no server process |

### Ingest (§4)
| Package | Role |
|---|---|
| `httpx` | async HTTP — board APIs fetched concurrently |
| `tenacity` | retry/backoff on every external call (§13) |
| `trafilatura` | readability extraction, tier 3 of §4.2 |
| `extruct` | JSON-LD `JobPosting` parsing, tier 2 of §4.2 |
| `lingua-language-detector` | DE/EN detection; more accurate than `langdetect` on short text |

### Storage & output
| Package | Role |
|---|---|
| `sqlite3` (stdlib) | no ORM — SQLAlchemy is overhead at this scale |
| `ruamel.yaml` | `master_profile.yaml` — preserves comments on round-trip, `pyyaml` destroys them |
| `typst-py` | CV/cover PDF rendering (§10) without subprocess plumbing |

### Ops
| Package | Role |
|---|---|
| `langfuse` | tracing (§12); self-hostable. LangSmith is the hosted alternative |
| `structlog` | JSON logs keyed by `run_id` / `job_id` |
| `typer` | CLI: `add`, `ingest`, `run`, `review`, `resume`, `cost`, `digest` |
| `rich` | terminal diff rendering for `jobpilot review` |

### Dev
`uv` · `pytest` · `pytest-asyncio` · `ruff` · `mypy`

No eval framework. The §14 golden set is plain pytest assertions — a framework for 20 cases is
more configuration than value.

### Deliberately excluded
| Not used | Why |
|---|---|
| `langchain` (meta-package) | `langgraph` + `langchain-core` covers it; the meta-package pulls a large unused tree |
| `langchain-community` | heavy transitive deps, nothing here needs it |
| `langchain-openai` | embeddings are local by design (§5.2) |
| `langchain-huggingface` | `sentence-transformers` is called directly |
| `langchain-groq` | single-provider decision (§12) |
| `pyresparser` | unmaintained, brittle deps. One-time CV extraction is an LLM call, not a library |
| `playwright` / headless browser | not in the ingest path (§4.2 tier 4). Phase 6 only, and there it is Claude in Chrome |
| SQLAlchemy / Alembic | 4 tables, one writer. Plain SQL + a thin repo layer |

### Environment

```bash
ANTHROPIC_API_KEY=...          # the only model key required
LANGFUSE_PUBLIC_KEY=...        # optional, Phase 4
LANGFUSE_SECRET_KEY=...
GITHUB_TOKEN=...               # optional, raises repo-doc fetch rate limit (§5.3)
ADZUNA_APP_ID=...              # Phase 4
ADZUNA_APP_KEY=...
```


## Changes from v1

| v1 | v2 | Why |
|---|---|---|
| 4 generic nodes | ingest graph + 14-node tailor graph | v1's "Tailor" node discarded the entire 5-role pipeline |
| no state schema | typed `JobState` with reducers | untraceable past node 3; concurrent writes silently overwrite |
| `interrupt` mentioned | `SqliteSaver` wired in | **interrupt does not work without a checkpointer** — v1 would fail at runtime |
| blob cosine similarity | per-requirement extraction + evidence retrieval | blob similarity rates "both are tech docs" highly; gives no actionable gap report |
| `score < X → skip` | deterministic hard-gap gate | a missing hard requirement is categorical, not a low score |
| "diff-check bullets" | deterministic fact validator + unit tests | the guardrail that matters cannot be a prompt instruction |
| OpenAI embeddings | local `bge-m3` | no OpenAI key in `.env`; German JDs need multilingual |
| `docxtpl` | Typst | v1's renderer could not produce the required two-column PDF cleanly |
| URL dedupe | content-hash dedupe | reposted jobs re-tailored every run |
| no evals | golden set + 5 metrics | prompt changes regress silently |
| no observability | tracing + cost ledger + budget guard | unattended spend with no visibility |
| no batch runner / no limits | §19 runner + limit guard, backpressure on review queue |
| no human interface | §20 Cowork control plane: schedule, review Artifact, notifications |
| memory undefined | §21 five typed stores, per-node scoping, human-approved writes |
| auto-apply implied | §22 assisted fill, hard stop before attestations + submit |
| aggregators only | §4.1 career-page watcher via ATS board APIs — where Werkstudent roles actually live |
| URL ingestion unspecified | §4.2 four-tier extraction, raw payload retained |
| multi-provider (Groq + Anthropic) | Anthropic-only, Haiku/Sonnet tiering — same routing, one SDK |
| Germany/Werkstudent absent | prefilter node | CLAUDE.md's priority rules were entirely missing from v1 |
| "ATS score > 95" asserted by LLM | ATS score ≥95 **computed** from 5 counted components + fabrication gates (§6) | self-graded number is theater; counted components can't be inflated, and the evidenced-terms denominator removes the keyword-stuffing gradient |

## Sources
- https://developer.adzuna.com/
- https://arbeitnow.com/api/job-board-api
- https://developer.usajobs.gov/api-reference/get-api-search
- https://langchain-ai.github.io/langgraph/how-tos/human_in_the_loop/
- https://typst.app/docs/
