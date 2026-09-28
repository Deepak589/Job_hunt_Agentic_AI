#!/usr/bin/env python3
"""build_cv_data.py - regenerate data/cv_data.json from data/master_profile.yaml.

master_profile.yaml is the single source of truth (plan.md §5.1, §21.3). This script
is the ONE-DIRECTION generator that keeps cv_data.json a mechanical render of it, so
cv_data.json is never hand-edited. Bullets are a plain concatenation of outcome /
metric / method - no invented phrasing, nothing that isn't already in the yaml.

Usage:  python3 scripts/build_cv_data.py   # overwrites data/cv_data.json
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agentic_ai.profile import Profile  # noqa: E402
from agentic_ai.render import _fmt_month  # noqa: E402

SKILL_LABELS = {
    "languages": "Languages",
    "llm_rag": "LLM & RAG",
    "deep_learning": "Deep Learning",
    "mlops_cloud": "Cloud & DevOps",
    "full_stack": "Full-Stack",
    "machine_learning": "ML & Data",
    "tools": "Tools",
}


def _bullet_sentence(b: dict) -> str:
    outcome = b["outcome"].strip()
    text = outcome[0].upper() + outcome[1:]
    metric = b.get("metric")
    method = (b.get("method") or "").strip()
    if metric:
        text += f" ({metric})"
    if method:
        text += f" — {method}."
    else:
        text += "."
    return text


def _org_line(entry: dict) -> str:
    org = entry["company"]
    clients = entry.get("clients")
    if clients:
        org += " · clients: " + ", ".join(c.split(" (")[0] for c in clients)
    return org


def _project_meta(entry: dict) -> str:
    parts = list(entry.get("stack", []))
    if entry.get("live_url"):
        parts.append("live at " + entry["live_url"].replace("https://", "").replace("http://", ""))
    parts.append(entry["repo"])
    return " · ".join(parts)


def build(profile: Profile) -> dict:
    raw = profile.raw
    identity = raw["identity"]
    constraints = raw["constraints"]
    ps = raw["profile_summary"]

    profile_text = (
        f"{ps['core'].strip()} "
        + " ".join(f"{p.strip()[0].upper()}{p.strip()[1:]}." for p in ps["proof_points"])
        + f" {ps['backing']}. Looking for {ps['seeking']}."
    )

    projects = [
        {
            "title": entry["name"],
            "meta": _project_meta(entry),
            "bullets": [
                _bullet_sentence(b) for b in entry["bullets"] if b.get("status") != "not_shipped"
            ],
        }
        for entry in raw["projects"]
    ]
    experience = [
        {
            "title": entry["title"],
            "org": _org_line(entry),
            "dates": f"{_fmt_month(entry['start'])} – {_fmt_month(entry['end'])}",
            "bullets": [
                _bullet_sentence(b) for b in entry["bullets"] if b.get("status") != "not_shipped"
            ],
        }
        for entry in raw["experience"]
    ]

    return {
        "name": identity["name"].upper(),
        "tagline": [k.upper() for k in identity["tagline_keywords"]],
        "photo": "assets/photo.jpg",
        "contact": [
            ["mail", identity["email"]],
            ["phone", identity["phone"]],
            ["pin", identity["location"]],
            ["link", identity["github"]],
            ["link", identity["linkedin"]],
        ],
        "skills": [
            [SKILL_LABELS[cat], ", ".join(s["name"] for s in items)]
            for cat, items in raw["skills"].items()
        ],
        "education": [
            [
                e["degree"],
                f"{e['institution']}, {e['location']}",
                f"{_fmt_month(e['start'])} – {_fmt_month(e['end'])}",
            ]
            for e in raw["education"]
        ],
        "certifications": [c["name"] for c in raw["certifications"]],
        "languages": [[lang["name"], lang["level"]] for lang in constraints["languages"]],
        "profile": profile_text,
        "projects": projects,
        "experience": experience,
        "main_sections": [
            {"kind": "projects", "items": projects},
            {"kind": "experience", "items": experience},
        ],
    }


def main() -> None:
    profile = Profile.load(ROOT / "data/master_profile.yaml")
    data = build(profile)
    out_path = ROOT / "data/cv_data.json"
    out_path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
