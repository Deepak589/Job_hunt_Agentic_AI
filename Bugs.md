Full list, all confirmed by reading code/data directly — 4 issues, ranked by impact:

Bug 1 — fact validator blind to your own profile summary (validators/facts.py)
allowed_tech (the set deciding "is this word evidenced or fabricated") is built only from profile.all_tech(), all_stack(), and each bullet's outcome/method text. It never reads profile_summary.proof_points — where your profile literally says "owned Kubernetes, CI/CD and AWS layer of a cloud project". Verified live: 'cloud' in allowed_tech → False. Real, true content gets flagged as a hallucination.

Bug 2 — brittle word-by-word allowlist for eligibility keywords (validators/facts.py + nodes/validate_facts.py)
extract_requirements.md rule 10 tells the JD-extractor to emit bare generic words for disqualifiers ("degree", "student", "enrolled"...) — that's intentional, for matching against your eligibility facts elsewhere. But validate_facts reuses that same list to check for fabrication, a different question entirely. A patch on 09-29 hardcoded exceptions for the specific words that broke that day (ai, it, germany, student). Today a new JD said "Degree in..." and it broke the same way — 'degree' in eligibility_terms → False. This will keep happening word-by-word forever until the category error (disqualifier keywords ≠ fabrication-checkable tech) is fixed at the root.

Combined impact of #1+#2: 17 of 31 runs in this project's entire history (55%) died to this exact failure class — the single biggest cause of rejection, ahead of real disqualifiers (8) and real hard-gate misses (6).

Bug 3 — never_claim is a dead/empty feature, and its error message lies about its source (profile.py, cv_extract.py, validators/facts.py)
never_claim: [] in your yaml — always empty, because cv_extract.py:366 hardcodes it empty on every profile-init and nothing ever populates it. Yet when a draft claims German fluency, the error says "never_claim forbids this" — false. The actual check reads constraints.languages (a different field), and facts.py's own docstring admits it: "Broader never_claim enforcement (arbitrary prose claims) is not attempted here." So: the mechanism that caught the German claim is real and correct, but the error message points at a feature that doesn't exist yet. Confusing, not dangerous.

Bug 4 — not code, data: master_profile.yaml is marked UNREVIEWED in its own header and is missing real experience (OpenAI, prompt engineering) — separate from the above, this is on the input side, not the validator.

Priority: fix 1+2 first (they're the volume driver), 3 is cosmetic, 4 is your data to add. Want me to fix 1 and 2 now?
