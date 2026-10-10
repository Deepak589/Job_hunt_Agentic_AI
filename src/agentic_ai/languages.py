"""CEFR level comparison for language requirements.

Replaces the binary "is this language below fluency" test that drove both the coverage
rail and the fact validator. That test could not express the only language situation that
actually occurs here: a real but partial level, improving. It therefore did two wrong
things at once — skipped a job whose JD asked for basic German when the profile already
had more than basic, and flagged the honest sentence "my German is A2, currently working
toward B1" as a fabricated fluency claim, because it only looked for the word "german".

Deterministic on purpose. A language level is a fact about the candidate, not a judgment
call, so it stays in code and overrides whatever the model decided (repo CLAUDE.md:
"Code then re-decides a language below the profile's level ... whatever the model said").
"""

from __future__ import annotations

import functools
import re

CEFR = ("a1", "a2", "b1", "b2", "c1", "c2")

# Word-level labels the profile may use instead of a CEFR code.
LABEL_TO_CEFR = {
    "native": "c2", "muttersprache": "c2", "mother tongue": "c2", "bilingual": "c2",
    "fluent": "c1", "fliessend": "c1", "fließend": "c1", "proficient": "c1",
    "advanced": "c1", "business fluent": "c1", "verhandlungssicher": "c1",
    "upper intermediate": "b2", "intermediate": "b1", "conversational": "b1",
    "elementary": "a2", "basic": "a2", "beginner": "a1",
}

# What a JD's phrasing DEMANDS, lowest-to-highest. Checked highest-first, so "sehr gute
# Deutschkenntnisse" resolves to b2 and not to the b1 that its own substring "gute" would
# match. Ordering is the whole correctness story here.
DEMAND_PHRASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("c2", ("muttersprachlich", "muttersprache", "native speaker", "native-level", "native level",
            "mother tongue", "erstsprache", "c2")),
    ("c1", ("verhandlungssicher", "business fluent", "fluen", "fließend", "fliessend",
            "professional proficiency", "full professional", "exzellent", "excellent command", "c1")),
    ("b2", ("sehr gute", "sehr gut", "very good", "upper intermediate", "strong command", "b2")),
    ("b1", ("gute", "gut", "good", "solide", "intermediate", "conversational", "mittelstufe",
            "working knowledge", "working proficiency", "b1")),
    ("a2", ("grundkenntnisse", "grundlegende", "basic", "basis", "elementary", "einfache", "a2")),
    ("a1", ("anfänger", "beginner", "first steps", "a1")),
)

# A sentence may name a level ABOVE the held one when it is explicitly framed as in
# progress and that level matches the profile's `in_progress` field.
LEARNING_MARKERS = (
    "learning", "learn", "lerne", "lernen", "working toward", "working towards", "toward",
    "towards", "currently", "in progress", "studying", "studium", "improving", "aiming",
    "preparing", "enrolled in", "on track", "progressing", "building toward",
)


# A JD written in German names the language in German, so matching the profile's own
# spelling ("German") against JD text found nothing on exactly the postings this rail
# exists for. Patterns, not substrings, because the country and the language collide:
# plain "german" matches "Germany" ("authorized to work in Germany" was being read as a
# language demand) and plain "deutsch" matches "Deutschland".
LANG_PATTERNS: dict[str, tuple[str, ...]] = {
    "german": (r"\bgerman\b", r"\bgermans\b", r"\bdeutsch(?!land)",),
    "english": (r"\benglish\b", r"\benglisch",),
    "french": (r"\bfrench\b", r"\bfranz(ö|o)sisch",),
    "spanish": (r"\bspanish\b", r"\bspanisch",),
    "hindi": (r"\bhindi\b",),
    "tamil": (r"\btamil\b",),
    "telugu": (r"\btelugu\b",),
}

# Kept for callers that want the bare spellings (the fact validator exempts a language the
# profile holds from the unevidenced-tech check).
LANG_ALIASES: dict[str, tuple[str, ...]] = {
    "german": ("german", "deutsch", "deutschkenntnisse"),
    "english": ("english", "englisch", "englischkenntnisse"),
    "french": ("french", "französisch"),
    "spanish": ("spanish", "spanisch"),
    "hindi": ("hindi",), "tamil": ("tamil",), "telugu": ("telugu",),
}


def mentions_language(text: str, language_name: str) -> bool:
    """Whether `text` names this language, in English or in German.

    Word-bounded: "Germany" is a country, "Deutschland" is a country, and neither is a
    language requirement.
    """
    t = text.lower()
    key = language_name.strip().lower()
    patterns = LANG_PATTERNS.get(key)
    if patterns is None:
        return re.search(r"\b" + re.escape(key) + r"\b", t) is not None
    return any(re.search(pat, t) for pat in patterns)


def level_index(level: str | None) -> int:
    """Position on the CEFR scale, or -1 for an unknown/absent level."""
    if not level:
        return -1
    v = str(level).strip().lower()
    if v in CEFR:
        return CEFR.index(v)
    for label, cefr in LABEL_TO_CEFR.items():
        if label in v:
            return CEFR.index(cefr)
    m = re.search(r"\b([abc][12])\b", v)
    return CEFR.index(m.group(1)) if m else -1


def normalize_level(level: str | None) -> str | None:
    i = level_index(level)
    return CEFR[i] if i >= 0 else None


def held_level(lang_record: dict) -> str | None:
    """The level the profile actually claims. `in_progress` is deliberately NOT folded in:
    a level being studied is not a level held, so it never raises coverage. It only widens
    what a draft may honestly SAY (see `claim_error`)."""
    return normalize_level(lang_record.get("level"))


def demanded_level(text: str) -> str | None:
    """The CEFR floor a requirement's wording demands, or None if it names no level.

    None means the JD mentioned a language without qualifying it ("German and English
    required"). That is treated as the lowest useful bar by callers, not as C1 — reading an
    unqualified mention as 'fluent' is what produced false skips.
    """
    t = text.lower()
    for cefr, phrases in DEMAND_PHRASES:
        if any(re.search(r"\b" + re.escape(p), t) for p in phrases):
            return cefr
    return None


def claimed_level(sentence: str) -> str | None:
    """The level a draft sentence asserts, or None if it asserts none."""
    return demanded_level(sentence)


def has_learning_marker(sentence: str) -> bool:
    s = sentence.lower()
    return any(m in s for m in LEARNING_MARKERS)


@functools.lru_cache(maxsize=1)
def _cefr_set() -> frozenset[str]:
    return frozenset(CEFR)


def profile_languages(raw: dict) -> list[dict]:
    return list(raw.get("constraints", {}).get("languages", []) or [])


def language_verdict(text: str, raw: dict, max_gap_steps: int) -> str | None:
    """"met" | "unknown" | "unmet" for the language demand in `text`, or None if it makes none.

    One requirement often names two languages ("gute Deutsch- und Englischkenntnisse"), and
    the weakest one decides — that was the original rail's whole purpose and it is kept: a
    `_keyword_hit` any-match on "english" once cleared a German-mandatory posting.

    A mention with no level attached ("German and English required") is NOT read as
    fluency. Reading it that way is what skipped jobs the candidate could do.
    """
    order = {"met": 0, "unknown": 1, "unmet": 2}
    worst: str | None = None
    for rec in profile_languages(raw):
        name = str(rec.get("name", ""))
        if not name or not mentions_language(text, name):
            continue
        held = held_level(rec)
        demanded = demanded_level(text)
        if demanded is None:
            # No level named. Only a level already at C1+ can satisfy an unspecified
            # demand; anything less is a question for review, never an assumed pass.
            verdict = "met" if level_index(held) >= CEFR.index("c1") else "unknown"
        else:
            gap = level_index(demanded) - level_index(held)
            if gap <= 0:
                verdict = "met"
            elif gap <= max_gap_steps:
                verdict = "unknown"  # a review question, never a silent skip
            else:
                verdict = "unmet"
        if worst is None or order[verdict] > order[worst]:
            worst = verdict
    return worst


def claim_error(sentence: str, raw: dict) -> str | None:
    """The fabrication a draft sentence commits about a language, or None.

    Only a level ABOVE the held one is an error, and only when it is asserted rather than
    framed as in progress. So "completed A2 and currently working toward B1" passes when
    the profile records `in_progress: B1`, while "fluent in German" fails. The old check
    errored on the mere presence of the word "german", which made the honest sentence
    prompts/agent_cv.md asks for impossible to write.
    """
    for rec in profile_languages(raw):
        name = str(rec.get("name", ""))
        if not name or not mentions_language(sentence, name):
            continue
        claimed = claimed_level(sentence)
        if claimed is None:
            continue  # named the language, asserted no level — nothing to fabricate
        held_i = level_index(held_level(rec))
        if level_index(claimed) <= held_i:
            continue
        in_prog = normalize_level(rec.get("in_progress"))
        if in_prog and claimed == in_prog and has_learning_marker(sentence):
            continue  # "working toward B1" with B1 recorded as in progress
        return (
            f"claims {name} at {claimed.upper()} but the profile holds "
            f"{(held_level(rec) or 'none').upper()}"
            + (f" (in progress: {in_prog.upper()})" if in_prog else "")
        )
    return None
