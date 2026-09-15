#!/usr/bin/env python3
"""profile_sync.py - drift check between data/master_profile.yaml and data/cv_data.json.

master_profile.yaml is the single source of truth (plan.md 21.3).
cv_data.json is a rendering of it. This asserts the rendering has not drifted.

Usage:  python3 scripts/profile_sync.py   # prints failures only; exit 1 on drift
"""
import json, re, sys, pathlib

try:
    import yaml
    from agentic_ai.profile import NUMERIC_RE, Profile
except ImportError as e:
    sys.exit(f"{e} - run inside the project venv (uv sync)")

ROOT = pathlib.Path(__file__).resolve().parent.parent
Y = yaml.safe_load((ROOT / "data/master_profile.yaml").read_text())
C = json.loads((ROOT / "data/cv_data.json").read_text())
P = Profile.load(ROOT / "data/master_profile.yaml")

failures = []

def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())

# A yaml skill name may appear on the CV under a longer label.
# left = yaml name, right = accepted CV spelling it is folded into.
ALIASES = {
    "reranking": "cross-encoder reranking",
    "hybrid retrieval": "hybrid retrieval (BM25 + dense, RRF)",
    "AWS EC2": "EC2",
    "AWS RDS": "RDS",
    "Docker Compose": "Docker Compose",
}

def cv_skill_tokens():
    """Flatten CV skill strings into atoms, splitting parentheticals too."""
    atoms, labels = set(), set()
    for _, line in C["skills"]:
        for chunk in re.split(r",(?![^(]*\))", line):
            chunk = chunk.strip()
            if not chunk:
                continue
            labels.add(chunk)
            atoms.add(chunk)          # keep the full label too, e.g. "Linux (Ubuntu)"
            m = re.match(r"^(.*?)\s*\((.*)\)$", chunk)
            if m:
                atoms.add(m.group(1).strip())
                for inner in m.group(2).split(","):
                    atoms.add(inner.strip())
            else:
                atoms.add(chunk)
    return atoms, labels

def check_skills():
    cv_atoms, cv_labels = cv_skill_tokens()
    cv_norm = {norm(a) for a in cv_atoms}
    y_names = [s["name"] for cat in Y["skills"].values() for s in cat]

    missing = []
    for n in y_names:
        if norm(n) in cv_norm:
            continue
        if norm(ALIASES.get(n, "")) in cv_norm:
            continue
        missing.append(n)
    if missing:
        failures.append(("yaml skill not on CV", missing))

    y_norm = {norm(n) for n in y_names} | {norm(v) for v in ALIASES.values()}
    extra = []
    for label in cv_labels:
        if norm(label) in y_norm:
            continue
        base = re.sub(r"\s*\(.*\)$", "", label).strip()
        if norm(base) in y_norm:
            continue
        extra.append(label)
    if extra:
        failures.append(("CV skill not in yaml", sorted(extra)))

def check_metrics():
    """Every number the CV states must trace to a metric in master_profile.yaml.

    EXACT membership against Profile.all_metrics(). The previous substring test
    (`n in a`) passed a fabricated "9" on "0.9", "40" on "740" and "13" on "13,582" -
    a false negative for every number that is a substring of a real one.
    """
    allowed = P.all_metrics()
    blob = json.dumps({k: C[k] for k in ("profile", "projects", "experience")})
    # strip dates and version-ish model names before scanning
    blob = re.sub(r"\b(19|20)\d{2}\b", " ", blob)          # years
    blob = re.sub(r"ViT-B/\d+|amd\d+|arm\d+|linux/\w+", " ", blob)
    blob = re.sub(r"PostgreSQL\s+\d+", "PostgreSQL 16", blob)
    found = {t for part in blob.replace("/", " ").split() for t in NUMERIC_RE.findall(part)}
    bad = sorted(n for n in found if n not in allowed and n.rstrip("%") not in allowed)
    if bad:
        failures.append(("number on CV not traceable to a profile metric", bad))

def check_never_claim():
    """not_shipped bullets must not surface on the CV."""
    blob = norm(json.dumps(C))
    for p in Y["projects"]:
        for b in p["bullets"]:
            if b.get("status") == "not_shipped":
                sig = norm(b["outcome"])[:40]
                if sig and sig in blob:
                    failures.append(("not_shipped bullet appears on CV", [b["id"]]))

def check_links():
    for p in Y["projects"]:
        meta_blob = " ".join(c["meta"] for c in C["projects"])
        for key in ("repo", "live_url"):
            val = p.get(key)
            if not val:
                continue          # optional: not every project has a repo or a live URL
            if val.split("//")[-1] not in meta_blob:
                failures.append((f"{key} in yaml but not on CV", [f"{p['id']} -> {val}"]))

def check_identity():
    i, cvc = Y["identity"], dict(C["contact"])
    for label, a, b in [
        ("name", i["name"].upper(), C["name"]),
        ("email", i["email"], cvc.get("mail")),
        ("phone", i["phone"], cvc.get("phone")),
        ("location", i["location"], cvc.get("pin")),
    ]:
        if a != b:
            failures.append((f"identity.{label} mismatch", [f"yaml={a!r} cv={b!r}"]))

def check_meta():
    if not Y["meta"].get("reviewed_by_human"):
        failures.append(("meta.reviewed_by_human is false", ["CV is not cleared to send"]))

for fn in (check_skills, check_metrics, check_never_claim, check_links, check_identity, check_meta):
    fn()

if failures:
    print("DRIFT")
    for title, items in failures:
        print(f"  {title}:")
        for it in items:
            print(f"    - {it}")
    sys.exit(1)

print("OK - cv_data.json matches master_profile.yaml")
