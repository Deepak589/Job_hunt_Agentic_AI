#!/usr/bin/env bash
# Tier 1 - SAFE. Untracks the junk, adds .gitignore + README, normal push.
# History is NOT rewritten, so a fresh clone still downloads ~221MB.
# Run tier 2 afterwards if you want the clone to actually be small.
#
# Usage:  cd /path/to/ML_Final_project && bash 1-clean-working-tree.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -d .git ] || { echo "run this from inside the ML_Final_project checkout"; exit 1; }

git rev-parse --abbrev-ref HEAD
read -rp "Clean this repo on the branch above? [y/N] " ok
[ "$ok" = "y" ] || exit 1

cp "$HERE/gitignore.txt" .gitignore
cp "$HERE/README.md" README.md

# untrack, keep on disk
for p in images food_recipe graphify-out anaconda_projects; do
  git rm -r --cached --quiet "$p" 2>/dev/null || true
done
git rm --cached --quiet outputs/recipe_metadata.csv 2>/dev/null || true
git ls-files -z '*.pptx' '*.ipynb.bak' | xargs -0 -r git rm --cached --quiet

git add .gitignore README.md
git status --short | head -20
echo "... ($(git status --porcelain | wc -l | tr -d ' ') changed entries)"

git commit -q -m "Add README, untrack dataset and tooling output

The Kaggle images, raw CSV, graphify cache and slide decks are no longer
tracked. They stay on disk; .gitignore keeps them out from here on."
echo
echo "committed. push with:  git push"
