"""retrieve_evidence, score_coverage, hard_gap_gate — deterministic, no LLM (plan.md §6).

Two signals per requirement, because each covers the other's blind spot:

- **semantic** catches paraphrase ("build search over documents" vs a RAG bullet)
- **keyword** catches the ATS-literal match a real employer filter does, and rescues
  short exact terms the embedder dilutes ("JWT", "Alembic") inside a long bullet

The gate is a RULE, not a threshold. A JD wanting 4 years of Rust with zero Rust evidence
is categorically different from a low score; collapsing it into `score < 0.6` loses the
distinction and spends drafting calls on jobs that cannot be won.
"""

from __future__ import annotations

import functools
import re
from statistics import mean

from .config import settings
from .evidence import retrieve_many
from .profile import Profile
from .state import JobState, Requirement, Scores

# Terms that legitimately answer an eligibility/logistics requirement, read from the
# profile's own `constraints`, `identity` and `education` — not invented here. Without
# these, a disqualifier like "must be authorized to work in Germany" has no bullet to
# match and routes a perfectly applicable job to skip.
ELIGIBILITY_FIELDS = ("work_status", "relocation", "available_from", "full_time_from")


def _eligibility_terms(raw: dict, full_time: bool = False) -> set[str]:
    """Phrasings a JD uses for eligibility facts the profile already states.

    A synonym layer over real fields, never new facts: enrollment comes from an education
    record with status `in_progress`, the hours figure from
    `constraints.max_hours_per_week_in_term`, the degree names from `education`.

    Phrase-level on purpose. Splitting degrees into loose words would put "data",
    "science" and "engineering" in the set individually, and the multi-word rule would
    then read "data engineering" as covered.
    """
    terms: set[str] = set()
    constraints = raw.get("constraints", {})

    enrolled = [e for e in raw.get("education", []) if e.get("status") == "in_progress"]
    if enrolled:
        terms |= {
            "enrolled", "currently enrolled", "student", "matriculated",
            "master", "masters", "master's", "msc", "m.sc.",
            "master's programme", "masters programme", "master's program",
            "masters program", "master's degree", "masters degree", "graduate student",
        }
    for edu in raw.get("education", []):
        degree = str(edu.get("degree", ""))
        terms.add(degree)
        # "MSc Data Science" -> "data science"; "BTech — Computer Science & Engineering"
        # -> "computer science & engineering", "computer science"
        field = re.sub(r"^\s*(msc|m\.sc\.|ma|bsc|btech|b\.tech|ba|bachelor|master)\b[\s—–-]*", "", degree, flags=re.I)
        terms.add(field)
        terms.add(field.split("&")[0])

    # The hours cap binds only a werkstudent/part-time search. On a full-time job any
    # figure is answerable, so the bare phrases go in and no number gates the match.
    # Otherwise every figure at or below the cap is answerable and nothing above it is:
    # emitting only the cap would miss a JD asking for 20h when he can do 24, and
    # emitting a bare "hours per week" would let a 40h JD through a 24h cap.
    if full_time:
        cap = FULL_TIME_HOURS
        terms |= {"full time", "full-time", "hours per week", "hours a week", "permanent"}
    else:
        cap = constraints.get("max_hours_per_week_in_term")
        if cap:
            terms |= {"part time", "part-time"}
    for n in range(1, int(cap or 0) + 1):
        terms |= {
            f"{n} hours", f"{n}h", f"{n} h", f"{n} hours per week",
            f"{n}h/week", f"{n} hours/week", f"up to {n} hours",
        }
    return terms


def _singular(word: str) -> str:
    """Crude plural fold. Correctness comes from applying it to BOTH sides.

    The profile says "embeddings", a JD says "embedding"; the profile says "REST APIs", a
    JD says "APIs". Folding both through the same function makes those meet. It mangles
    irregulars ("kubernetes" -> "kubernete") and that is fine — the set and the query mangle
    identically. Only genuine collisions would hurt, and the 4-char floor avoids "aws"/"aw".

    No carve-out for "-is" endings: it would swallow "apis", and folding "analysis" to
    "analysi" costs nothing when the query folds the same way.
    """
    if len(word) >= 4 and word.endswith("s") and not word.endswith(("ss", "us")):
        return word[:-1]
    return word


def _normalize(term: str) -> str:
    cleaned = re.sub(r"[^a-z0-9+#./]+", " ", term.lower()).strip()
    return " ".join(_singular(w) for w in cleaned.split())


# A language requirement is answerable only at professional fluency. The profile lists
# German at A1/A2, and `never_claim` forbids claiming German professional fluency — so
# "German" must NOT enter the keyword set, or a "fluent German (C1)" disqualifier would
# match it and a job requiring German would sail through the gate.
FLUENT_LEVELS = ("fluent", "native", "c1", "c2", "bilingual")

# Upper bound for the generated hours phrasings in a full-time search. Not a claim about
# hours worked — just the range of figures a JD might name that he can answer.
FULL_TIME_HOURS = 60


@functools.lru_cache(maxsize=1)
def unclaimable_languages() -> frozenset[str]:
    """Languages the profile lists below professional fluency.

    Keeping these out of `keyword_set` is not enough. "Fluency in English and German is
    required" is ONE requirement carrying TWO languages, and `_keyword_hit` is an any-match:
    `english` alone marked it covered and a German-mandatory job cleared the gate. A named
    language below fluency blocks the whole requirement, whatever else in it matches.
    """
    langs = Profile.load().raw.get("constraints", {}).get("languages", [])
    return frozenset(
        _normalize(lang["name"])
        for lang in langs
        if not any(lvl in str(lang.get("level", "")).lower() for lvl in FLUENT_LEVELS)
    )


def _blocked_language(req: Requirement) -> str | None:
    """The unclaimable language this requirement demands, or None.

    Reads the requirement TEXT, not just `keywords` — on the Bain posting the extractor
    emitted only `english` for a clause naming both languages.
    """
    words = set(_normalize(req.text).split())
    for lang in unclaimable_languages():
        if lang in words:
            return lang
    return None


@functools.lru_cache(maxsize=2)
def keyword_set(full_time: bool = False) -> frozenset[str]:
    """Everything the profile can answer a literal keyword match with.

    `full_time` lifts the weekly-hours cap — see `_eligibility_terms`.
    """
    profile = Profile.load()
    terms: set[str] = set()
    terms |= profile.all_tech()  # 58 skill names
    terms |= profile.tags()  # 102 bullet tags
    terms |= profile.all_stack()  # stack entries on experience/project records
    terms |= profile.all_titles()
    terms |= profile.all_orgs()

    raw = profile.raw
    constraints = raw.get("constraints", {})
    for field in ELIGIBILITY_FIELDS:
        if constraints.get(field):
            terms.add(str(constraints[field]))
    if constraints.get("werkstudent_eligible"):
        terms |= {"werkstudent", "working student", "part-time", "student"}
    for lang in constraints.get("languages", []):
        if any(lvl in str(lang.get("level", "")).lower() for lvl in FLUENT_LEVELS):
            terms.add(lang["name"])
    # location: answers "must be based in / authorized to work in <place>"
    terms |= {p.strip() for p in str(raw["identity"]["location"]).replace("(", ",").replace(")", ",").split(",")}
    for edu in raw.get("education", []):
        terms |= {edu["institution"], str(edu.get("location", ""))}
    terms |= _eligibility_terms(raw, full_time=full_time)

    return frozenset(t for t in (_normalize(t) for t in terms) if len(t) > 1)


# Words that carry no ATS signal of their own — they grade or glue a keyword rather than
# name anything. Dropped before the all-words test below, never added to the term set.
# Without this, "fluent in english" failed where the bare "english" matched: one filler
# word is enough to sink an otherwise exact keyword.
KEYWORD_FILLER = frozenset({
    "a", "an", "the", "in", "of", "with", "and", "or", "for", "to", "on",
    "experience", "experienced", "knowledge", "skill", "skills", "proficiency",
    "proficient", "fluent", "fluency", "strong", "good", "very", "solid", "basic",
    "first", "hands", "hands-on", "understanding", "familiarity", "familiar",
})


# Facts about Deepak that master_profile.yaml does not record, and no CV bullet ever will.
# The evidence store cannot answer them, so they came back uncovered and the gate read that
# as failed — Bundesbank skipped at 100% hard coverage on a semester count and a grade
# average he may well satisfy. Absence of a fact is not a negative fact: these route to a
# question instead of a skip.
UNRECORDED_FACT_PATTERNS = (
    r"\bsemester\b",
    r"\bfachsemester\b",
    r"grade point average|\bgpa\b|notendurchschnitt|grade average|minimum grade",
    r"transcript|notenübersicht|academic record",
    r"driver'?s licen[cs]e|führerschein",
    r"criminal record|police clearance|führungszeugnis",
)


def _is_unrecorded_fact(req: Requirement) -> bool:
    text = req.text.lower()
    return any(re.search(p, text) for p in UNRECORDED_FACT_PATTERNS)


# A demand to be physically somewhere. Answerable only against WHERE THE JOB IS, which
# lives on the Job, not in the requirement text: Bain's clause says "a client location or
# your Bain home office" and names no city at all. Matching such text against the profile
# always failed, so every on-site job skipped — including Berlin ones he can reach.
ONSITE_PATTERNS = (
    r"on[- ]?site|onsite|in person|in the office|vor ort|präsenz",
    r"days? (a|per) week (at|in|from)|relocat|based in|work from our",
    # Mubea phrased it as an ability, not a presence: "Ability to work regularly in
    # Attendorn". It skipped for the right reason by accident — a Berlin job worded the
    # same way would have skipped too.
    r"(able|ability|possibility) to work (regularly |on a regular basis )?(in|at|from)",
    r"regelmäßig (in|vor)|wohnort|willing to commute|live (in|near)",
)


def _is_onsite(req: Requirement) -> bool:
    text = req.text.lower()
    return any(re.search(p, text) for p in ONSITE_PATTERNS)


# Words that appear inside a location string without narrowing it to a place he can reach.
# "Germany" above all: every LinkedIn location ends in it, so keeping it would make Munich
# and Mannheim both read as commutable from Potsdam.
GENERIC_PLACE_WORDS = frozenset({"germany", "deutschland", "area", "region", "metropolitan", "greater"})


@functools.lru_cache(maxsize=1)
def location_terms() -> frozenset[str]:
    """Single place words he can work from, from the profile's own location fields."""
    raw = Profile.load().raw
    places = [str(raw["identity"]["location"]), str(raw.get("constraints", {}).get("relocation", ""))]
    words = {w for p in places for w in _normalize(p).split()}
    return frozenset(w for w in words if w not in GENERIC_PLACE_WORDS and len(w) > 2)


def _location_reachable(job_location: str, terms: frozenset[str]) -> bool | None:
    """True if the job sits somewhere he can work from, False if not, None if unknown.

    None is the honest answer for a JD pasted without `--location`: an on-site clause is
    not evidence against a candidate, it is a question about which office.
    """
    if not job_location.strip():
        return None
    words = set(_normalize(job_location).split())
    return any(t in words for t in terms if " " not in t)


def _keyword_hit(req: Requirement, terms: frozenset[str]) -> str | None:
    """The matched term, or None. Whole-token match only.

    Substring matching here would be the same class of bug as the old metric check: "java"
    would hit on "javascript", "r" on every word containing it.
    """
    for kw in req.keywords:
        k = _normalize(kw)
        if not k:
            continue
        if k in terms:
            return k
        # a multi-word JD keyword is satisfied if every word that names something is known
        words = [w for w in k.split() if w not in KEYWORD_FILLER]
        if not words:
            continue
        if len(words) == 1 and words[0] in terms:
            return words[0]
        if len(words) > 1 and all(w in terms for w in words):
            return k
    return None


# ------------------------------------------------------------------------ graph nodes


# Where a requirement stops being one idea. Splitting on these lets a long clause be
# retrieved by its parts without becoming several requirements.
CLAUSE_SPLIT = re.compile(r"[;:,()]|\se\.g\.\s|\sand\s|\sor\s|\bincluding\b|/")
MAX_SUBQUERIES = 6


def subqueries(text: str) -> list[str]:
    """The full requirement, plus each clause inside it.

    An embedding of four ideas at once resembles none of them: "Solid backend
    fundamentals: HTTP APIs, async, databases, version control, testing" scored 0.49
    against a CV holding every part. Retrieving by the parts and keeping the best hit
    fixes that WITHOUT splitting the requirement itself — one requirement still counts
    once at the gate, and its keyword anchor still spans the whole text.
    """
    parts = [p.strip() for p in CLAUSE_SPLIT.split(text)]
    clauses = [p for p in parts if len(p) > 3 and any(w not in KEYWORD_FILLER for w in p.lower().split())]
    if len(clauses) < 2:  # already one idea
        return [text]
    return [text, *clauses[: MAX_SUBQUERIES - 1]]


def retrieve_evidence(state: JobState) -> dict:
    """Per requirement, top-k bullets from the evidence store.

    Each requirement is queried by its whole text AND by each clause in it; the best hits
    across all of them become its evidence. Still one batched encode for the whole JD.
    """
    queries, spans = [], []
    for r in state.requirements:
        subs = subqueries(r.text)
        spans.append((len(queries), len(queries) + len(subs)))
        queries.extend(subs)

    hits = retrieve_many(queries)
    reqs = []
    for r, (lo, hi) in zip(state.requirements, spans):
        merged = {}
        for ev in (e for group in hits[lo:hi] for e in group):
            if ev.source_id not in merged or ev.similarity > merged[ev.source_id].similarity:
                merged[ev.source_id] = ev
        best = sorted(merged.values(), key=lambda e: -e.similarity)[: settings.top_k]
        reqs.append(r.model_copy(update={"evidence": best}))

    return {
        "requirements": reqs,
        "notes": [
            f"retrieved evidence for {len(reqs)} requirements "
            f"({len(queries)} queries, k={settings.top_k})"
        ],
    }


def score_coverage(state: JobState) -> dict:
    """Mark each requirement covered, then compute the deterministic coverage scores."""
    terms = keyword_set(full_time=state.job.employment_type == "fulltime")
    places = location_terms()
    scored: list[Requirement] = []
    for r in state.requirements:
        blocked = _blocked_language(r)
        hit = None if blocked else _keyword_hit(r, terms)
        semantic = not blocked and r.best_similarity >= settings.sem_threshold
        covered = semantic or hit is not None
        unknown = not covered and r.type == "disqualifier" and _is_unrecorded_fact(r)

        # An on-site clause is decided by where the job is, never by the CV.
        if r.type == "disqualifier" and not covered and _is_onsite(r):
            reachable = _location_reachable(state.job.location, places)
            covered, unknown = reachable is True, reachable is None

        scored.append(
            r.model_copy(
                update={
                    "covered": covered,
                    "unknown": unknown,
                    # semantic wins the label when both fire: it means a specific bullet
                    # actually backs the requirement, which is the stronger claim
                    "covered_by": "semantic" if semantic else ("keyword" if hit else None),
                    "matched_term": "" if semantic else (hit or ""),
                }
            )
        )

    hard = [r for r in scored if r.type == "hard"]
    soft = [r for r in scored if r.type == "soft"]
    return {
        "requirements": scored,
        "scores": Scores(
            hard_coverage=mean(r.covered for r in hard) if hard else 1.0,
            soft_coverage=mean(r.covered for r in soft) if soft else 1.0,
            semantic_fit=round(mean(r.best_similarity for r in scored), 4) if scored else 0.0,
        ),
        "notes": [
            f"coverage: hard {sum(r.covered for r in hard)}/{len(hard)}, "
            f"soft {sum(r.covered for r in soft)}/{len(soft)}"
        ],
    }


def gate_reason(state: JobState) -> str | None:
    """Why this job is a skip, or None to proceed. Pure — the routing fn wraps it."""
    unmet = state.unmet_disqualifiers()
    if unmet:
        return "disqualifier: " + "; ".join(r.text for r in unmet)

    blocking = state.uncovered_hard()
    if len(blocking) > settings.max_blocking_hard_gaps:
        return (
            f"{len(blocking)} uncovered hard requirements "
            f"(limit {settings.max_blocking_hard_gaps}): "
            + "; ".join(r.text for r in blocking)
        )
    return None


def log_skip(state: JobState) -> dict:
    reason = gate_reason(state)
    return {"skip_reason": reason, "notes": [f"SKIP — {reason}"]}


def hard_gap_gate(state: JobState) -> str:
    """Conditional edge: 'skip' or 'proceed'."""
    return "skip" if gate_reason(state) else "proceed"
