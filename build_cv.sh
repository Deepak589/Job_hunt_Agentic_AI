#!/usr/bin/env bash
# Render the two-column CV from data/cv_data.json + templates/cv_two_column.html
# Requires: pip install jinja2 (already a project dep); Chrome/Chromium/Edge installed.
# Usage: ./build_cv.sh [output.pdf]
set -euo pipefail
cd "$(dirname "$0")"
OUT="${1:-main_cv_v2.pdf}"
python3 - "$OUT" <<'PY'
import sys, json, subprocess, shutil, tempfile
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape

root = Path(".")
data = json.loads((root / "data" / "cv_data.json").read_text())
env = Environment(loader=FileSystemLoader(str(root / "templates")), autoescape=select_autoescape(["html"]))
photo_uri = f"file://{(root / 'templates' / 'assets' / 'photo.jpg').resolve()}"
html = env.get_template("cv_two_column.html").render(d=data, photo_uri=photo_uri)

with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
    f.write(html)
    html_path = f.name

for c in (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    shutil.which("chromium") or "", shutil.which("google-chrome") or "",
):
    if c and Path(c).exists() or shutil.which(c or ""):
        chrome = c
        break
else:
    sys.exit("No Chrome/Chromium found — install one or set CHROME=/path/to/chrome")

out = sys.argv[1]
subprocess.run([chrome, "--headless", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
                 f"--print-to-pdf={out}", f"file://{html_path}"], check=True)
Path(html_path).unlink()

try:
    from pypdf import PdfReader
    n = len(PdfReader(out).pages)
    print(f"{out}: {n} page(s)" + ("" if n == 1 else "  <-- FIX: must be 1 page"))
except ImportError:
    print("(install pypdf to auto-check page count)")
PY
echo "wrote $OUT"
