Open bugs — verified against HEAD 7c91290 on 2026-10-09 (code, jobpilot.db, checkpoints.db, jobpilot.log.jsonl).
Fixed items are removed (branch slim-agent): fact validator ignoring profile_summary, bare-word eligibility keywords hitting the
fabrication check, rewrite schema collapsing to {} (now DraftOut, flat bullets list), MCP stderr-over-stdout, raw tracebacks from `add --review`, msgpack checkpoint warnings.
Ranked by impact.

Bug 2 — validate_facts passes an empty draft (nodes/validate_facts.py)
`rewrite` now raises on 0 bullets, but validate_facts itself still returns "clean" for a draft with none (e.g. an edited
draft at review). Fix: error on 0 bullets, and on any profile bullet missing from the draft (rewrite must include them all).
(recruiter_sim, which had a related scan bug, was removed in the slim-agent pipeline.)

Bug 3 — MCP add_job blocks for minutes, then the client gives up (src/agentic_ai/mcp/server.py)
A full run takes 4+ min; the MCP client stops waiting at ~4 min while `_CLI_TIMEOUT_S` is 300. The run still completes
and is logged, but the caller never sees the result (Manova run, 2026-10-08).
Also operational: the stdio server is a long-lived process. The 14:22 and 19:28 fixes only apply after Claude Desktop
is restarted; until then add_job keeps returning stderr noise.
Fix: add_job returns a job_id immediately and runs the CLI in the background; poll get_job. Lower the timeout under
the client's.

Bug 4 — add_job drops job metadata (mcp/server.py `add_job`)
Accepts `jd_text` XOR `url`, never both, and passes no title/company/location. Every MCP-added row in jobpilot.db has
blank title, company and url (source "manual"), so reviews and the job list are unreadable.
Fix: optional title, company, location, url params passed to `jobpilot add`.

Bug 5 — requirement extraction wording rules now live in prompts/analyze.md (slim-agent branch)
Unverified live: "runden Dein Profil ab" -> soft, and "Flask, Django or FastAPI" -> three soft items. Run the golden
set (evals/) after the first real run; there is no baseline.json yet.

Bug 6 — test suite is coupled to the private profile and stale (tests/test_fact_validator.py)
7 tests call `profile.by_id("proj.rag_pipeline.b1")` / `proj.cloudnotes.b1`. After the 2026-09-30 profile regen the ids
are long slugs (`proj.rag_pipeline_hand_rolled_retrieval_to_corrective_rag_crag.b1`), so `by_id` returns None. The tests
also load the gitignored real `data/master_profile.yaml`. `pytest -q` is therefore not clean (CLAUDE.md definition of done).
Fix: point the tests at a committed fixture built from `master_profile.example.yaml`.
(test_graph_phase3 needs Chrome for render; set CHROME where none is installed — environment, not a bug.)

Bug 7 — never_claim is a dead feature and its error message lies about its source (profile.py, cv_extract.py:366, validators/facts.py:112)
`never_claim: []` in data/master_profile.yaml (line 535) because cv_extract.py hardcodes it empty on every profile-init
and nothing populates it. When a draft claims German fluency, the error says "never_claim forbids this", but the actual
check reads `constraints.languages`, and facts.py's own docstring says broader never_claim enforcement is not attempted.
The guard is real; the message points at a feature that doesn't exist. Confusing, not dangerous.
Fix: reword the message to name `constraints.languages`, or make never_claim real.

Bug 8 — profile data gap (data/master_profile.yaml) — your data, not code
`meta.reviewed_by_human` is now true, but real experience is still missing: OpenAI usage and prompt engineering do not
appear anywhere in the profile. JDs asking for them can never be covered. (The "UNREVIEWED" line in the file header
is stale generator text.)

Bug 9 — data/jobpilot.log.jsonl is tracked in git (.gitignore)
It is not ignored, so every run dirties the tree and it was committed in 7c91290. It holds per-job skip reasons and
costs. CLAUDE.md says run data stays out of the repo.
Fix: add it to .gitignore and `git rm --cached data/jobpilot.log.jsonl`.

Priority: 1 and 2 together (nothing downstream works until drafts have bullets), then 3 and 4 (MCP usability), then 5.
