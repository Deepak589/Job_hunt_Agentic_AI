# analyze — v1

Read one job description and the candidate's profile, then do the three jobs a human
applicant does before touching a CV: list what the posting asks for, check each item against
the profile, and note what already matches. One pass.

## 1. List the requirements

Build this list from the JD text alone. Never add or drop an item because of what the
profile contains. One item per requirement. Every technology, tool, framework and language is its own item
(Python, SQL, Docker, Git: one each), even when the JD lists them together. The degree, each
spoken language, the location and the weekly hours are separate items too. Keep a stated
quantity with its item ("3+ years of Python"). Quote or closely paraphrase the JD. Don't
invent requirements. Skip the company blurb, benefits, EEO text and how to apply.

Buckets, decided by the wording:
- **hard**: stated flatly ("required", "must", "you have", or listed with no hedge). Named
  stack, tools, degree, years.
- **soft**: hedged or optional ("von Vorteil", "wünschenswert", "preferably", "a plus",
  "nice to have", "runden Dein Profil ab"), plus traits, culture and tone. "Flask, Django or
  FastAPI" or "such as ..." means any one will do: each is soft.
- **disqualifier**: a gate the JD draws flatly: language level, work authorization,
  enrollment status, clearance, or where the work happens (on-site city).

`keywords` are lowercase ATS terms: the JD's spelling plus obvious variants. Never empty.

## 2. Check each one against the profile

Give every requirement one verdict:
- **met**: the profile's facts satisfy it, even in different words. A named tool shown in a
  project or coursework counts when the JD asks for no years or production depth.
- **unmet**: a confirmed gap. For a disqualifier: the profile states a failing fact (German
  listed at A2 against "very good German"). For a hard or soft item: a plain absence you can
  state in one sentence. Silence about a disqualifier is never a failing fact.
- **unknown**: the profile neither confirms nor contradicts it (a personal fact or a tool it
  never mentions). It becomes a question for the candidate, not a skip.

Torn between `unmet` and `unknown`: choose `unknown`. A false skip costs a real application;
a question costs one line. `met` needs a profile fact you can name. Never invent one.
`evidence` is one short line: the profile fact or bullet id that backs it, or the absence.

## 3. Matches

`matches`: what the profile covers well, naming the specific project or bullet that proves
it ("hand-rolled hybrid retriever validated against a LangChain rebuild", not "Python").
Order by how much this JD rewards it. `positioning_mismatch`: one line if the CV's default
framing (headline, section order) misfits this JD, else null.

## Output

Call the `AnalysisOut` tool exactly once.
