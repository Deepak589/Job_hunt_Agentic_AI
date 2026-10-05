# MCP layer: Claude Desktop + Chrome-assisted apply — design

2026-10-05. Personal/single-user only — not a hosted/multi-tenant service.

## Goal

Let Claude Desktop drive this pipeline conversationally, and use the **Claude in Chrome**
extension to fill (never submit) job application forms with the pipeline's tailored,
fact-checked output.

## Non-goals / hard boundaries

- **Never auto-submits.** The human click on the final Submit button is the structural
  checkpoint this project is built around (`CLAUDE.md`: "It never applies"). The MCP layer
  only fills forms; it has no "click submit" tool and no path to one.
- **No site-specific scraping/filling code.** Claude-in-Chrome reads whatever page is open
  and maps fields itself from data the MCP server hands it. No Greenhouse/Lever/Workday/
  LinkedIn-specific selectors — zero per-site maintenance, at the cost of fill accuracy
  depending on Claude reading each page correctly.
- **Not multi-tenant.** Single local process, your own `.env` key, your own
  `master_profile.yaml`. No auth layer, no network exposure.
- **LinkedIn scraping/automation carries real ToS/ban risk** (flagged to user during
  brainstorming) — this design doesn't special-case it; the user accepts that risk
  per-site, same as any other site Claude-in-Chrome is pointed at.

## Architecture

```
Claude Desktop (chat)
   │
   ├── MCP tools (this design) ──stdio──> jobpilot-mcp server (local process)
   │                                          ├── shells out to `jobpilot` CLI
   │                                          │   (add / review / applied)
   │                                          └── reads data/jobpilot.db,
   │                                              master_profile.yaml, out/ directly
   │                                              (read-only, no graph logic duplicated)
   │
   └── Claude-in-Chrome tools (separate, pre-existing) ──> your real Chrome
          navigates, reads page, fills form fields, uploads CV file
          (Claude in Desktop chat is the only thing that sees both tool sets
           and chains MCP data into Chrome actions — the two never talk directly)
```

Transport: stdio (standard local MCP server pattern — Claude Desktop spawns the process).
No network listener, no auth needed.

State-changing tools shell out to the existing `jobpilot` CLI, same reasoning `CLAUDE.md`
already documents for Cowork: the CLI is the one orchestration path; a second one (e.g.
re-importing LangGraph nodes directly) would be unversioned and untestable. Read-only
tools query `data/jobpilot.db` / `master_profile.yaml` / `out/` directly since they report
state rather than run pipeline logic.

## Tools

| Tool | Returns | Implementation |
|---|---|---|
| `list_pending_reviews()` | `[{job_id, company, title, score}]` for jobs at the `human_review` checkpoint | read `data/jobpilot.db` |
| `get_job(job_id)` | JD text, diagnosis summary, draft (profile_line, bullets, cover_letter), score, CV/cover-letter PDF paths (if rendered), job URL | read `data/jobpilot.db` + `out/<job_dir>/` |
| `get_applicant_profile()` | name, email, phone, location, github, linkedin | read `master_profile.yaml` `identity` block |
| `add_job(jd_text?, url?)` | `{job_id, status}` | shell: `jobpilot add --file <tmp>` or `--url` |
| `review_job(job_id, action, edits?)` | updated status; on approve, triggers render+score through to completion | shell: `jobpilot review <job_id>` (+ non-interactive edit/approve/reject flags) |
| `mark_applied(job_id)` | confirmation | shell: `jobpilot applied <job_id>` |

No tool exists to submit a form, click a button, or post anything — that stays entirely in
Claude-in-Chrome's hands, driven by the user's own "ok I'm submitting" instruction in chat.

## Data flow (concrete walkthrough)

1. JD text (from chat or a Claude-in-Chrome read of an open job page) → `add_job` → CLI
   runs the full graph to the `human_review` interrupt → returns `job_id`.
2. `get_job(job_id)` → structured JSON shown to user in chat for review.
3. User approves → `review_job(..., action="approve")` → CLI resumes, renders CV/cover
   letter PDFs via the Chrome-render pipeline (existing, unrelated Chrome instance used
   headlessly for PDF generation — not the same as Claude-in-Chrome) → returns PDF paths
   + ATS score.
4. `get_applicant_profile()` → contact fields.
5. Claude now holds: PDF paths, contact fields, JD/company/title context — all in its own
   context window, no file transferred over MCP itself (paths only; Claude-in-Chrome reads
   the actual PDF off disk when uploading).
6. Claude-in-Chrome fills the open form using that data. Stops before Submit.
7. User clicks Submit themselves, tells Claude → `mark_applied(job_id)` logs it.

## Error handling

- CLI shell-outs: non-zero exit → return `{error: <stderr tail>}` as the tool's text
  result, never a raw exception/traceback to Claude. Covers budget-cap hits
  (`JOBPILOT_MAX_DAILY_COST_USD`), fact-validation failures, hard-gap skips — all already
  produce CLI-level messages today; this just relays them.
- DB/profile reads: missing file or empty result → a clear message ("no pending reviews",
  "profile not found at <path>"), not an exception.

## Testing

`tests/test_mcp_server.py` — mock `subprocess.run` for the shelling-out tools, point
DB/profile reads at tmp fixture files, same pytest conventions as the rest of the suite.
No new test framework or fixtures beyond what's already used elsewhere in the repo.

## Files touched

- New: `src/agentic_ai/mcp/server.py`, `src/agentic_ai/mcp/__init__.py`,
  `tests/test_mcp_server.py`
- `pyproject.toml`: add `mcp` SDK dependency + `jobpilot-mcp` console-script entry point
- Docs: a short README/CLAUDE.md note on how to point Claude Desktop's config at the
  server (`uv run --directory <repo> jobpilot-mcp`)

## Open question for implementation plan

`jobpilot review` is currently interactive (prompts in terminal). `review_job` needs a
non-interactive path (flags for action + edits) — the plan should check whether that
already exists or needs adding to the CLI.
