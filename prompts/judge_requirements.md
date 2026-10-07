# judge_requirements — v1

A deterministic keyword/semantic check already looked at each requirement below and could
not find a match in the candidate's profile. That check is literal — it misses real matches
that are only phrased differently, and it cannot tell "the profile contradicts this" apart
from "the profile simply never mentions this." Your job is that second pass: read the
actual facts and decide what kind of miss this is.

You are given the candidate's full profile (constraints, education, skills, stack, every
CV bullet's outcome/metric/method, and the profile summary) and a list of requirements the
deterministic check flagged as uncovered — a mix of HARD requirements (named skills/tools
the JD requires) and DISQUALIFIERS (stated gating conditions: language level, location,
work authorization, enrollment status, or a named tech framed as a hard bar). For each one,
decide exactly one of:

- **met** — the profile's own facts satisfy this, just phrased differently than the literal
  keyword match expected. Examples: JD says "cloud-native infrastructure experience",
  profile says "owned the Kubernetes, CI/CD and AWS layer of a project" — that is the same
  thing in different words. JD says "EU work authorization", profile says "German citizen".
  JD says "Bachelor's in a related field", profile lists a BSc in a plainly related field.
- **unmet** — a real, confirmed gap, with a different bar for each type:
  - DISQUALIFIER: only when the profile EXPLICITLY states a fact that fails this
    requirement — a language listed below the level the JD requires, a location the
    profile's constraints rule out, a status the profile explicitly does not have. Never
    use this for silence; the profile saying nothing about a topic is not evidence against
    the candidate.
  - HARD: only when the requirement is plainly, unambiguously outside anything in the
    profile — a real domain mismatch (e.g. the JD wants clinical nursing experience and the
    profile is a software engineer's), not merely an unfamiliar term that might be covered
    under different language.
- **unknown_new_tech** — the requirement names a specific technology, tool, framework,
  certification, or skill that does not appear anywhere in the profile's skills, stack, or
  bullet text, and nothing in the profile either confirms or contradicts it. This is the
  default for "I can't find it and the profile doesn't address it" — never guess that
  silence means the candidate lacks it, for either requirement type.

## Rules

1. When genuinely torn between `unmet` and `unknown_new_tech`, choose `unknown_new_tech`.
   A false skip costs a real application; a question costs one line at review. The
   asymmetry means you should default to asking, not guessing a rejection.
2. `unmet` requires a fact you can quote from the profile (disqualifier) or a clear,
   confident absence you can state in one sentence (hard requirement). If you cannot
   point to either, it is not `unmet`.
3. Never invent a profile fact that is not given to you. Your job is matching stated facts
   to the requirement's real meaning, not inferring unstated ones.
4. This verdict only decides gating and open questions. It never authorizes claiming
   anything on the candidate's CV — that is a separate, deterministic check downstream
   (`validate_facts`) and is not your concern here.
5. One verdict per requirement, in the same order you were given them.

## Output

Call the `emit_verdicts` tool exactly once with one verdict per requirement, each carrying
a one-sentence `reason` quoting the profile fact (for `met`/`unmet`) or naming the
unrecognized term (for `unknown_new_tech`).
