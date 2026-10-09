How extraction worked (same every time)
Input: JD text pasted in or fetched by me. No parser, script or schema. I read it line by line.
Buckets (from the CLAUDE.md Diagnosis step):
Hard requirements: named stack, degree, language level, location, hours.
Soft requirements: nice-to-haves, tone, seniority.
Disqualifiers: explicit “hard requirement” lines.
Matches: each one backed by CV or repo evidence.
Positioning: JD title against CV tagline.
Hard vs soft test: wording decides.
Flat and unhedged means hard. Example: AURELIUS “Sehr gute Deutsch- und Englischkenntnisse”.
Hedged means soft. Examples: “von Vorteil”, “or großes Interesse” (n8n).
ATS keywords: I copied JD phrases verbatim, and only where the claim was already true. Avelios got “configuration”, “reliable”, “secure”, “reproducible”, “scalable” and “REST API endpoints”. “Microservice” was deliberately left out.
Evidence check: each hard requirement was checked against the CV template or repos, then marked match, soft gap or blocking gap.
Final check: keyword survival after PDF text extraction (pypdf and poppler).