# rewrite — v3

Rewrite the candidate's CV bullets and cover letter for ONE job, from the diagnosis and the
candidate's real profile. The profile is your only source of facts.

## Bullets: Google X-Y-Z

"Accomplished [X] as measured by [Y] by doing [Z]."
- **X** — a concrete outcome, not a task.
- **Y** — the number from the cited bullet's `metric` field. If it is null, skip the Y
  clause. Never invent a number.
- **Z** — the specific tool or method from the cited bullet's `method` field.
- Start with a strong verb (Built, Reduced, Deployed, Automated, Improved, Trained,
  Architected). No passive voice, no "responsible for", no "worked on".
- Keep each bullet under 220 characters so the CV stays on one page.

## Cite a real source

Every bullet sets `source_bullet_id` to the exact id of the profile bullet it is built from.
Recombine the outcome, metric and method of ONE bullet. Never merge two bullets, and never
use a number that isn't that bullet's own `metric`. If the bullet's `status` is
`not_shipped`, write designed, built or prototyped — never imply it is live.

## What to do

1. Follow the given `section_order`. Don't invent an order.
2. Include everything. Every bullet of every experience entry and every project in the
   profile goes into the CV: drop nothing, merge nothing, add nothing. One output bullet per
   profile bullet, each `source_bullet_id` used once. You change only the wording and the
   order. Order by this JD: in each section put the roles or projects that best answer the
   JD's hard requirements first, and inside each one the strongest bullets first. Projects
   in `diagnosis.matches` go near the top of their section. Which section leads is already
   set by `section_order`.
3. Write a profile line of 2-3 sentences that leads with what THIS JD rewards.
4. JD wording: if the profile backs a JD term (its skills, stack, a bullet's text, the
   summary), use the JD's spelling — that is what an ATS scans for. That includes the
   umbrella a posting uses for work the profile really did: "LLMs", "Generative AI" and
   "Agentic AI" are backed by LangChain, LangGraph, RAG, Ollama and the multi-agent
   pipeline, and the validator resolves them through exactly those terms
   (`validators/aliases.py`), so the JD's word for them is not a fabrication. If the
   profile does NOT back a term, leave it out and say what was actually built in plain
   words: no "microservices" for something that isn't, and no "Kubernetes" on a bullet
   whose work never touched it.
5. Cover letter: name the company and role; the 2-3 most relevant projects or roles; why
   this company and why Germany; availability (working student now, full-time after the
   MSc). If `diagnosis.hard_gaps` is non-empty, name the gap in one sentence and say why the
   candidate is still a fit — don't dodge it. Plain, direct prose: no filler, no "I am
   excited to", no buzzwords. Write in English, unless the posting itself is in German; then
   write the letter in German.
6. Never claim anything on the `never_claim` list, or a language level above what the profile
   lists. Never imply German fluency.
   When the posting names German at any level, the letter MUST carry exactly one line stating
   the position — "My German is at A2 and I am currently learning B1." — placed near the end,
   never the opening. Don't apologise for it, don't expand it into a paragraph, don't promise a
   timeline, and don't treat it as a reason the application may not fit. A2 is held; B1 is only
   ever "learning" or "working toward". Staying silent about a German requirement reads worse
   than one modest line. A validator checks the level you claim, not whether you mention the
   language. If the whole letter is in German: "Mein Deutsch ist auf A2-Niveau, aktuell lerne
   ich B1." 

## If you are given prior validation errors

Fix only what the errors name — each one points at an exact bullet and an exact false claim.
Keep everything else from the prior attempt.

## Output

Return one draft. `bullets` is a single flat list in print order — the most relevant role or
project first, its strongest bullets first. Do not group by section; the section is taken
from each bullet's `source_bullet_id`.
