#!/usr/bin/env bash
# Downloads the papers referenced in docs/system_design_review_2026-09-18.md.
# Run from anywhere: bash docs/papers/fetch_papers.sh
set -euo pipefail
cd "$(dirname "$0")"
fetch() { curl -sSL --retry 3 -o "$2.pdf" "$1" && echo "ok  $2.pdf"; }
fetch https://arxiv.org/pdf/2503.13657 2503.13657_why-multi-agent-llm-systems-fail-MAST
fetch https://arxiv.org/pdf/2604.02539 2604.02539_synapse-job-person-fit-llm-guided-resume-optimization
fetch https://arxiv.org/pdf/2602.18550 2602.18550_measuring-validity-llm-resume-screening
fetch https://arxiv.org/pdf/2609.16517 2609.16517_competence-preserving-resume-perturbations
fetch https://arxiv.org/pdf/2609.12002 2609.12002_can-we-trust-llm-judges-multi-judge-ensemble
fetch https://arxiv.org/pdf/2606.19544 2606.19544_reliability-without-validity-llm-as-judge
fetch https://arxiv.org/pdf/2406.07791 2406.07791_judging-the-judges-position-bias
fetch https://aclanthology.org/2025.findings-naacl.270.pdf naacl2025_human-and-llm-based-resume-matching
