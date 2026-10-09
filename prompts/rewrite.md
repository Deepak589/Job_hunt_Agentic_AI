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
   summary), use the JD's spelling — that is what an ATS scans for. If it doesn't, leave the
   term out and say what was actually built in plain words: "multi-agent pipeline", not
   "agentic systems"; no "microservices" for something that isn't.
5. Cover letter: name the company and role; the 2-3 most relevant projects or roles; why
   this company and why Germany; availability (working student now, full-time after the
   MSc). If `diagnosis.hard_gaps` is non-empty, name the gap in one sentence and say why the
   candidate is still a fit — don't dodge it. Plain, direct prose: no filler, no "I am
   excited to", no buzzwords. Write in English, unless the posting itself is in German; then
   write the letter in German.
6. Never claim anything on the `never_claim` list, or a language level above what the profile
   lists. Don't imply German fluency.

## If you are given prior validation errors

Fix only what the errors name — each one points at an exact bullet and an exact false claim.
Keep everything else from the prior attempt.

## Output

Return one draft. `bullets` is a single flat list in print order — the most relevant role or
project first, its strongest bullets first. Do not group by section; the section is taken
from each bullet's `source_bullet_id`.
