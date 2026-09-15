#!/usr/bin/env bash
# Render the two-column CV from data/cv_data.json + templates/cv_two_column.typ
# Requires: pip install typst
# Usage: ./build_cv.sh [output.pdf]
set -euo pipefail
cd "$(dirname "$0")"
OUT="${1:-main_cv_v2.pdf}"
python3 - "$OUT" <<'PY'
import sys, typst
# root must be the project dir so the template can read ../data/cv_data.json
typst.compile("templates/cv_two_column.typ", output=sys.argv[1], root=".")
PY
echo "wrote $OUT"
