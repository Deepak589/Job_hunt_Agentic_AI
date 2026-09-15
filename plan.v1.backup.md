# AI Job-Search & Resume-Tailoring Agent — Plan

## Architecture

LangGraph state machine (not a single prompt loop) — needed for branching, retries,
and a human-approval checkpoint before anything gets submitted.

Pipeline (4 nodes):
1. Ingest — pull job postings on a schedule
2. Score/Match — rank postings against your master profile
3. Tailor — rewrite resume + generate cover letter per JD
4. Output/Act — produce files, log, human approves before any submission

## Tool stack per stage

### 1. Job sourcing
Avoid LinkedIn scraping — ToS violation, IP bans, legally fragile for anything
meant to run unattended.

- Adzuna API — free tier, good coverage. MCP server already exists:
  https://github.com/folathecoder/adzuna-job-search-mcp
- USAJOBS API — for US federal/public roles: https://developer.usajobs.gov/api-reference/get-api-search
- JSearch (RapidAPI) — aggregates Indeed/LinkedIn/Glassdoor via licensed data
- Arbeitnow / RemoteOK APIs — remote/EU roles, no auth needed

### 2. Resume parsing / structured profile
- Master resume as structured JSON/YAML (skills, projects, experience bullets
  with metrics, tags) — single source of truth. Agent rewrites FROM this,
  never invents facts.
- `pyresparser` or one-off LLM extraction only to bootstrap this JSON from an
  existing PDF/docx.

### 3. Matching / scoring
- Embed JD + resume bullets (OpenAI/Anthropic embeddings or local
  `sentence-transformers`) → cosine similarity for relevance score.
- Keyword-overlap scorer (JD keywords vs resume keywords) as a second signal
  — mirrors how real ATS systems score, so optimize for both.
- Store scored jobs in a local vector DB (Chroma/FAISS) to dedupe and avoid
  re-scoring/re-applying to the same posting.

### 4. Resume tailoring (core LLM step)
- Prompt: master resume JSON + JD text → reordered/reworded bullets
  emphasizing matched skills, never fabricated ones.
- Constrain output to a JSON schema (LangChain structured output / Pydantic)
  so rendering is deterministic — don't let the LLM free-write the final doc.
- Guardrail: diff-check tailored bullets against master resume facts before
  accepting output (catch hallucinated numbers/titles).

### 5. Document generation
- Render structured JSON into docx/PDF via a template engine (`docxtpl`, or
  Jinja → HTML → WeasyPrint → PDF). Keeps formatting consistent across every
  application instead of asking the LLM to "format a resume."

### 6. Orchestration / scheduling
- LangGraph for the agent graph (conditional edges: "score too low → skip",
  "score high → tailor")
- Cron (or simple scheduler) to trigger ingest daily/weekly
- SQLite for state (jobs seen, applied, scores) — no need for Postgres at
  this scale

## Correction / risk to design around

Fully autonomous auto-apply is risky: ATS systems flag bot-submitted
applications, and you lose quality control on a rewritten resume nobody
reviewed. Better: agent produces "tailored resume + cover letter ready," then
a human-approval checkpoint before submission. LangGraph supports this
natively via `interrupt` before the final node.

## Optimization ideas

- Cache JD embeddings so re-runs don't re-embed unchanged postings.
- Track application outcomes (interview/rejection) in the SQLite store and
  feed that back into scoring weights over time — turns this into a feedback
  loop instead of a static pipeline.
- Use a cheap/fast model (Groq/Llama) for scoring at scale; reserve your best
  model (Claude/GPT) for the actual resume rewrite step — cost optimization
  given you're already multi-provider (Groq, OpenAI, Anthropic, HF).

## Sources
- https://github.com/folathecoder/adzuna-job-search-mcp
- https://developer.usajobs.gov/api-reference/get-api-search
- https://publicapis.io/best/jobs
- https://jobspipe.dev/blog/best-jobs-api-2026
