Checking the Job_finder folder, tracker, and application notes to see how each JD was extracted.

I can't see the old session transcripts. The raw JD text was never saved in `Applications/`, and `Examples/README.md` says so too. What follows is rebuilt from `reason.md` files, the tracker, and memory notes, and only 12 of the 22 application folders have notes at all.

## How extraction worked (same every time)
- **Input:** JD text pasted in or fetched by me. No parser, script or schema. I read it line by line.
- **Buckets** (from the CLAUDE.md Diagnosis step):
  - Hard requirements: named stack, degree, language level, location, hours.
  - Soft requirements: nice-to-haves, tone, seniority.
  - Disqualifiers: explicit "hard requirement" lines.
  - Matches: each one backed by CV or repo evidence.
  - Positioning: JD title against CV tagline.
- **Hard vs soft test:** wording decides.
  - Flat and unhedged means hard. Example: AURELIUS "Sehr gute Deutsch- und Englischkenntnisse".
  - Hedged means soft. Examples: "von Vorteil", "or großes Interesse" (n8n).
- **ATS keywords:** I copied JD phrases verbatim, and only where the claim was already true. Avelios got "configuration", "reliable", "secure", "reproducible", "scalable" and "REST API endpoints". "Microservice" was deliberately left out.
- **Evidence check:** each hard requirement was checked against the CV template or repos, then marked match, soft gap or blocking gap.
- **Final check:** keyword survival after PDF text extraction (pypdf and poppler).

## What was extracted per JD

| JD | Hard requirements | Gap / disqualifier |
|---|---|---|
| Avelios SWE Python | CS degree, Python, Java, Docker, GitHub, English | Java only coursework-level |
| AURELIUS | MSc, 16–20h, GenAI, API/MCP, German + English | **German, blocking**; no n8n (soft) |
| NOW GmbH | German B2 (JD not saved) | **German, blocking** |
| goaudio | Enrolled student **based in Munich**, Python, React, LLM, 15–20h | Munich (cleared by relocation); no GCP (soft) |
| Temedica | LangChain, agentic AI | Google ADK, regulated healthcare (soft) |
| SAP Kyma (460093) | LangGraph, MCP, A2A, RAG, K8s | A2A, zero evidence |
| BCG Applied AI | LangGraph, Claude API, MCP, REST | custom GPTs, n8n, Cursor (soft) |
| Retorio | owned SQL tables/views, tested transforms, agents/MCP | no dbt (soft) |
| valuemize | K8s, Docker, CI/CD, React/FastAPI/Postgres, TS, ML on ERP data | no standalone Node/Express |
| mellow NOIR | German C1, automation/integration | **German, hard**; no n8n/Make/Zapier |
| Lovehoney | legal/contract ops, LLM orchestration | zero legal-ops experience |
| Merantix | Python, JS/TS, REST, SQL, 20h, on-site Berlin | REST not GraphQL; JS not strict TS |

- **No JD record, only tracker notes (14):** SAP 457812, acemate, dida, Seven Senders, S-Kreditpartner, colayer, nerou, Funky Monkeys, maindtec, over99, Manova, Siemens, Snke, BMW.

## Weak spots in the extraction
- **Two misreads, caught later:**
  - Avelios: Java was first flagged as zero evidence because I trusted a memory note instead of the actual template.
  - goaudio: the Munich line was first called an unfixable blocker, but you were open to relocating.
- **No audit trail:** nothing stored lets us re-check an old call, and the keyword lists were never saved either.
- **Judgement, not a rule:** hard vs soft depended on my reading of the wording each time.

