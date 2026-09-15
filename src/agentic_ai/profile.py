"""Loader + validation over data/master_profile.yaml (plan.md §5.1).

master_profile.yaml is authoritative. This module is the only way the pipeline reads it,
so every consumer — retrieval, the coverage gate, the Phase 2 fact validator, and
scripts/profile_sync.py — agrees on what the profile contains.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel
from ruamel.yaml import YAML

from .config import settings

# One numeric-token regex for the whole codebase. Matches what a CV bullet can print:
# bare numbers, decimals, and suffixed forms (13.5K, 100x, 23%, 100+, 3.5GB).
# Thousands separators are part of the token so "13,582" does not read as "13" and "582".
#
# Deliberately NO `/\d+` suffix: with it, "alpha 0.3/0.5/0.7/0.9" matched as the single
# token "0.3/0" and silently swallowed three real values. Slash-joined figures are split
# by expand_metric_tokens instead, which is the safe direction — over-splitting yields
# extra allowed tokens, under-matching yields a number nothing validates.
NUMERIC_RE = re.compile(r"(?<![\w.,/-])\d+(?:,\d{3})*(?:\.\d+)?(?:[KkMmXx%+]|GB|MB)?")


def expand_metric_tokens(metrics: list[str] | set[str]) -> set[str]:
    """Every numeric surface form a set of declared metrics legitimises.

    Metric strings are compound ("2.5 years", "61% Recall@10", "alpha 0.3/0.5/0.7"), and
    a bullet may print any numeric token from one. So: keep the literal string, add each
    numeric token in it, and add each token's percent-stripped variant so a declared "60"
    covers a printed "60%".

    Callers must test **exact membership** against this set. The check this replaces used
    a substring test, under which a fabricated "9" passed on "0.9", "40" on "740" and
    "13" on "13,582" — a false negative for every number that is a substring of a real
    one, in a function whose whole job is catching fabricated numbers.
    """
    tokens: set[str] = set()
    for m in metrics:
        m = str(m).strip()
        if not m:
            continue
        tokens.add(m)
        # split slash-joined figures ("0.3/0.5/0.7") so each value is scanned on its own
        for part in m.replace("/", " ").split():
            tokens.update(NUMERIC_RE.findall(part))
    return tokens | {t.rstrip("%") for t in tokens}


class Bullet(BaseModel):
    id: str
    outcome: str
    metric: str | None = None
    method: str = ""
    tags: list[str] = []
    status: str | None = None  # "not_shipped" must never be phrased as delivered
    parent_id: str = ""  # exp.* or proj.* this bullet belongs to
    source: str = "cv_bullet"

    def as_evidence_text(self) -> str:
        """The text that gets embedded — outcome + metric + method (plan.md §5.2)."""
        return " ".join(p for p in (self.outcome, self.metric or "", self.method) if p).strip()


class Skill(BaseModel):
    name: str
    level: str
    category: str
    evidence: list[str] = []


class Profile(BaseModel):
    raw: dict
    bullets: list[Bullet]
    skills: list[Skill]

    # ---------- construction ----------

    @classmethod
    def load(cls, path: Path | None = None) -> Profile:
        yaml = YAML(typ="safe")
        raw = yaml.load((path or settings.profile_path).read_text())

        bullets: list[Bullet] = []
        for section in ("experience", "projects"):
            for entry in raw.get(section, []):
                for b in entry.get("bullets", []):
                    bullets.append(Bullet(**b, parent_id=entry["id"]))

        skills = [
            Skill(**s, category=cat)
            for cat, items in raw.get("skills", {}).items()
            for s in items
        ]
        return cls(raw=raw, bullets=bullets, skills=skills)

    # ---------- what the pipeline asks of it ----------

    @property
    def bullet_ids(self) -> set[str]:
        return {b.id for b in self.bullets}

    def by_id(self, bullet_id: str) -> Bullet | None:
        return next((b for b in self.bullets if b.id == bullet_id), None)

    def all_tech(self) -> set[str]:
        """Skill names, lowercased. The keyword half of the §6 two-signal match."""
        return {s.name.lower() for s in self.skills}

    def tags(self) -> set[str]:
        return {t.lower() for b in self.bullets for t in b.tags}

    def all_stack(self) -> set[str]:
        """Stack entries on experience/project records — tech not always in `skills`."""
        return {
            item.lower()
            for section in ("experience", "projects")
            for entry in self.raw.get(section, [])
            for item in entry.get("stack", [])
        }

    def declared_metrics(self) -> list[str]:
        """The hand-listed `allowed_metrics`. A cached render of the bullets, not truth."""
        return [str(m) for m in self.raw.get("allowed_metrics", [])]

    def all_metrics(self) -> set[str]:
        """Every numeric token a generated bullet may legitimately print.

        Derived from the bullets, which are the authority — `allowed_metrics` is a cached
        render of them (its own comment says "regenerate, don't hand-edit") and has
        already drifted. It is unioned in because a few real figures live outside bullet
        `metric` fields: experience `duration_years`, education grades, cert counts.

        Exact-match against this set; never substring.

        ponytail: token-level check, so a real "61" from "61% Recall@10" would also pass
        in a fabricated "61% faster". Bounding a number to its own metric string needs
        span-level validation — add it if the Phase 2 eval set shows that failure mode.
        """
        sources = [b.metric for b in self.bullets if b.metric]
        sources += self.declared_metrics()
        sources += [
            str(e[k])
            for e in self.raw.get("experience", [])
            for k in ("duration_years",)
            if e.get(k) is not None
        ]
        return expand_metric_tokens(sources)

    def all_titles(self) -> set[str]:
        return {
            e["title"].lower() for e in self.raw.get("experience", []) if e.get("title")
        }

    def all_orgs(self) -> set[str]:
        orgs = {e["company"].lower() for e in self.raw.get("experience", []) if e.get("company")}
        orgs |= {e["institution"].lower() for e in self.raw.get("education", []) if e.get("institution")}
        return orgs

    @property
    def never_claim(self) -> list[str]:
        return list(self.raw.get("never_claim", []))

    # ---------- validation ----------

    def validate_profile(self) -> list[str]:
        """Integrity errors in the yaml itself. Empty list = clean.

        Catches what nothing checked before: a typo in a skill's `evidence:` list points
        at no bullet, so that skill silently loses its backing and retrieval degrades
        with no error anywhere.
        """
        errors: list[str] = []
        ids = [b.id for b in self.bullets]

        for bid in {i for i in ids if ids.count(i) > 1}:
            errors.append(f"duplicate bullet id: {bid}")

        known = set(ids)
        for s in self.skills:
            for ref in s.evidence:
                if ref not in known:
                    errors.append(f"skill {s.name!r} cites unknown bullet id {ref!r}")

        for b in self.bullets:
            if not b.outcome.strip():
                errors.append(f"{b.id}: empty outcome")

        if not self.raw.get("meta", {}).get("reviewed_by_human"):
            errors.append("meta.reviewed_by_human is false — profile is not cleared to use")

        return errors

    def stale_declared_metrics(self) -> tuple[set[str], set[str]]:
        """Drift between the bullets and the cached `allowed_metrics` list.

        Returns (missing, unbacked): tokens the bullets print that the list omits, and
        list entries no bullet backs. Reported by `jobpilot profile check` as a
        regenerate-me signal, not as a validation error — the bullets are authoritative,
        so this drift never blocks a run. Today it is non-empty: the list was hand-edited
        and `jobpilot profile sync` does not exist yet.
        """
        def numeric_only(tokens: set[str]) -> set[str]:
            # compare bare numeric tokens: not the compound literals expand() also
            # carries, and not both the "60"/"60%" spellings of the same figure
            return {t.rstrip("%") for t in tokens if NUMERIC_RE.fullmatch(t)}

        declared = numeric_only(expand_metric_tokens(self.declared_metrics()))
        from_bullets = numeric_only(
            expand_metric_tokens([b.metric for b in self.bullets if b.metric])
        )
        return from_bullets - declared, declared - from_bullets
