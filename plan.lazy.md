# plan.lazy.md — the version that ships this week

Companion to `plan.md`, not a replacement. `plan.md` is the target architecture; this is the
subset that delivers the actual ask — *paste a JD, get a tailored CV + cover letter + an ATS
score you can trust* — with the least code that does it honestly.

**Rule for this doc:** anything that is not required to produce a sendable PDF from one JD is
out of scope. It stays in `plan.md` and gets built when the pain is real, not before.

---

## What ships

```
jobpilot apply jd.txt  ──>  out/<company>_cv.pdf
                            out/<company>_cover.pdf
                            ATS fact table on stdout
```

Two LLM calls. One skip gate. One fact validator. ~250 lines.

---

## What is deliberately cut, and the trigger to add it

| Cut | From | Add when |
|---|---|---|
| LangGraph state machine | §1–3 | the rewrite↔validate loop needs resuming, i.e. Step 6 below stops being a `for` loop |
| Chroma + bge-m3 evidence store | §5.2 | `master_profile.yaml` exceeds ~15k tokens (today: ~6k, fits in one prompt whole) |
| SqliteSaver checkpointer | §9 | there is a batch to resume. One job at a time needs no checkpoint |
| Job sources, ingest graph, prefilter | §4, §17 | you get tired of pasting. You paste ~3/day |
| Career-page watcher | §4.1 | Phase 4 as written |
| Batch runner, limit guard | §19 | more than one job per invocation |
| Cowork control plane, notifications | §20 | you stop being at the terminal when it runs |
| Procedural memory, edit clustering | §21.4 | hand-edit `preferences.yaml` until that's annoying 5+ times |
| Langfuse tracing, cost ledger | §12 | a verdict looks wrong and `--verbose` isn't enough |
| Circuit breakers, per-node timeouts | §13 | there is a cron job to protect |
| Assisted apply | §22 | plan.md already says never/last |

**Kept in full, no shortcuts** — these are the three that stop it being a toy:

- **hard-gap gate** (§6) — a missing hard requirement is categorical, not a low score
- **fact validator** (§8) — the only component whose failure sends a lie to an employer
- **computed ATS score** (§6) — counted facts, fabrication as a gate not a deduction

---

## Preconditions — fix before writing pipeline code

**P1. `scripts/profile_sync.py:98` substring match is a false-negative machine.**

```python
bad = [n for n in found if not any(n == a or n in a for a in allowed)]
#                                            ^^^^^^^^ this
```

`n in a` means `9` passes on `0.9`, `40` on `740`, `13` on `13,582`, `54` on `54.13`, `0.85` on
`0.854`. The fact validator inherits this function. §14 targets FN rate 0.00; today it's near 1.

→ exact match only. Expand `allowed_metrics` to every surface form the CV actually prints.
→ **verify:** `9`, `40`, `13`, `0.85`, `54` all rejected; every number currently on
   `main_cv_v2.pdf` still accepted; `profile_sync.py` still exits 0.

**P2. `master_profile.yaml:6-8` contradicts `profile_sync.py:3-5` on which file is authoritative.**

yaml header says the PDF wins; the script and `plan.md` §5.1/§21.3 say the yaml wins. Two of
three say yaml, and only the yaml can hold evidence ids, `never_claim` and atomized metrics.

→ rewrite the yaml header: yaml is truth, `cv_data.json` is a render of it.
→ **verify:** no file in the repo claims the PDF is authoritative.

**P3. `templates/cv_two_column.typ:4` hardcodes `json("../data/cv_data.json")`.**

Per-job renders need a per-job data file.

→ `#let d = json(sys.inputs.at("data", default: "../data/cv_data.json"))`
→ **verify:** `build_cv.sh` still produces text-identical output to today's `main_cv_v2.pdf`;
   a copied json at another path renders too.

**P4. `check_links` reports `live_url is null` for the 3 projects that have no live URL.**

Noise that trains you to ignore the NOTES block.

→ skip absent optional keys.
→ **verify:** `profile_sync.py` prints no NOTES section.

P1 is the one that matters. P2–P4 are ten minutes total.

---

## Build order

Each step ends runnable. Do not start the next until the verify passes.

### 1. `src/agentic_ai/profile.py` — load + index the yaml

Load `master_profile.yaml`. Expose what the rest of the pipeline asks of it:
`bullet_ids`, `all_metrics()`, `all_tech()`, `all_titles()`, `all_orgs()`, `never_claim`.
No embeddings, no vector store — the whole profile goes into the prompt.

→ **verify:** `all_tech()` returns 58 skills; `bullet_ids` returns 20; `all_metrics()` matches
   `allowed_metrics`. One `test_profile.py` asserting those three counts.

### 2. `src/agentic_ai/jd.py` — JD in

`--file` and `--stdin` only. URL fetch is §4.2 and can wait; you can copy-paste a posting.

→ **verify:** reads a saved JD, strips nothing important.

### 3. `requirements.py` — LLM call 1 (Haiku)

JD text → `[{text, type: hard|soft|disqualifier, keywords[]}]`, pydantic-validated.

**Sees only the JD.** Not the profile. §21.2 — a requirement extractor that knows your CV finds
the requirements it expects you to have, and the gap analysis downstream is then worthless.

→ **verify:** run on 3 real JDs by hand; the hard requirements it names match what you'd name.
   Save those 3 as the start of the golden set.

### 4. `coverage.py` — match, then gate. No LLM.

For each requirement: `covered = keyword literally in profile tech/tags` OR
`fuzzy match ≥ threshold`. Start with keyword + `difflib.get_close_matches`. That is enough for
58 skills; add embeddings when it visibly mismatches, not on principle.

```python
if any(r.type == "disqualifier" for r in reqs):     return "skip", reason
if len(uncovered_hard) > 1:                          return "skip", uncovered_hard
```

→ **verify:** a JD demanding Rust/Go/Scala exits with `skip` and prints the uncovered
   requirement, **before** any expensive call. This is the step that saves money and bad
   applications — test it explicitly.

**Ship point.** Steps 1–4 alone beat reading JDs by hand. Stop here for a day and use it.

### 5. `rewrite.py` — LLM call 2 (Sonnet)

In: whole profile + JD + gap report. Out:

```python
class Bullet(BaseModel):
    outcome: str
    metric: str | None
    method: str
    source_bullet_id: str      # load-bearing: makes step 6 possible
class Draft(BaseModel):
    profile_line: str
    section_order: list[str]   # ignored — set by code, see below
    bullets: dict[str, list[Bullet]]
    cover_letter: str
```

Section order is `SECTION_ORDER[role_type]`, a dict lookup, not the model's choice — it will be
inconsistent across applications otherwise. Role type from a keyword match on the JD title;
promote to a Haiku classifier only if keyword matching visibly misfires.

Note the template constraint: skills/education/certs/languages live in the sidebar and are
always visible. Order only varies for the main column — profile / projects / experience.

→ **verify:** output parses into `Draft`; every `source_bullet_id` exists in the profile.

### 6. `validators/facts.py` — deterministic. The guardrail.

```python
for bullet in draft:
    assert bullet.source_bullet_id in profile.bullet_ids
    for num  in extract_numerics(text):  assert num in profile.all_metrics()   # exact, see P1
    for tech in extract_tech(text):      assert tech.lower() in profile.all_tech()
    assert no never_claim phrase present
```

Failure → one retry with the offending tokens named in the prompt. Second failure → print the
errors and stop. Do not ship a draft that failed twice.

`extract_tech` needs a gazetteer — `plan.md` §8 names one but never says where it comes from.
Build it from `profile.all_tech()` plus the JD's own `keywords[]`. The JD terms are the point:
the likeliest hallucination is the model seeing "Kubernetes" in the JD and writing it in.

→ **verify:** `tests/test_facts.py` with adversarial cases, written *before* trusting the
   rewriter: an inflated percentage, a JD tech absent from the profile, a fabricated employer,
   an unknown `source_bullet_id`, a `never_claim` phrase, and the `proj.ra_nutrition.b3`
   `not_shipped` bullet phrased as delivered. All six caught, zero false negatives.

### 7. `render.py` — Draft → json → typst → pdf

Reuse `build_cv.sh`'s call and the existing template (now parameterized, P3). Cover letter gets
a second minimal `.typ` — one column, no sidebar.

→ **verify:** both PDFs open; CV is one page; no layout overflow.

### 8. `scoring/ats.py` — computed, per `plan.md` §6

```
GATES (any → total 0)   validator errors · disqualifier present
POINTS
  50 × covered_hard / total_hard
  20 × verbatim_hits / evidenced_jd_terms
  15 × pdf_fields_recovered / pdf_fields_expected
  10 × bullets_with_metric / bullets_metric_available
   5 × section order matches role type AND profile line targets it
```

`evidenced_jd_terms` is undefined in `plan.md` — define it here: **a JD keyword is "evidenced"
if `coverage.py` marked its requirement covered.** Terms with no evidence stay off the CV and
cost nothing; that property is what stops the score rewarding keyword stuffing.

Component 3 needs the PDF parseability check — re-extract with `pypdf` and assert name, email,
phone, location, every section heading and every skill survived. This is the only part of
"passing ATS" that maps to real employer software, and the template's `keep-terms()` hyphen
guard exists because of exactly this failure.

Print the fact table before the total. A score with no counts under it is invalid (CLAUDE.md).

→ **verify:** score the current `main_cv_v2.pdf` against a JD you'd genuinely apply to; the
   arithmetic reproduces by hand. Then confirm a draft with one fabricated number scores 0.

### 9. `cli.py` — `typer`, one command

```
jobpilot apply jd.txt [--role ai_engineer] [--verbose]
```

`--verbose` prints each prompt and response. That is the whole observability story until it
isn't enough.

→ **verify:** end to end on a real JD; you'd send the output.

---

## Dependencies

```
anthropic  pydantic  ruamel.yaml  typst-py  pypdf  typer  pytest
```

Seven. `plan.md` Appendix A lists 24 — the other 17 belong to the graph, the vector store, the
ingest layer and the ops layer, none of which exist yet.

No `langgraph` until step 5–6 becomes a real loop. No `chromadb` until the profile outgrows a
prompt. No `sentence-transformers` until keyword+fuzzy visibly fails — it pulls torch.

---

## Layout

```
src/agentic_ai/
  profile.py  jd.py  requirements.py  coverage.py  rewrite.py  render.py  cli.py
  validators/facts.py
  scoring/ats.py
prompts/         requirements.md  rewrite.md      # versioned, not inline strings
templates/       cv_two_column.typ  cover_letter.typ
tests/           test_profile.py  test_facts.py  test_ats.py
evals/golden/    the 3 JDs from step 3, grown to ~10
out/             generated PDFs, gitignored
```

Nine source files. `plan.md` §15's tree is the Phase-4 shape; grow into it, don't scaffold it.

---

## Done means

- a JD with a hard gap exits `skip` before spending a Sonnet call
- `tests/test_facts.py` catches all six adversarial cases
- the ATS fact table's arithmetic reproduces by hand
- you have sent an application built by it

Everything after that is `plan.md`, in its own order, when the pain is real.
