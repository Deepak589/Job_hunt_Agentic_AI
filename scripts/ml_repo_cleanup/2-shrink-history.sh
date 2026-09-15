#!/usr/bin/env bash
# Tier 2 - DESTRUCTIVE. Rewrites history to drop the 230MB of images and the
# other large blobs, then force-pushes. Clone size goes ~221MB -> ~1MB.
#
# This changes every commit SHA. Anyone with a clone (including you, elsewhere)
# must re-clone or hard-reset. Do tier 1 first and push it.
#
# Usage:  cd /path/to/ML_Final_project && bash 2-shrink-history.sh
set -euo pipefail

[ -d .git ] || { echo "run this from inside the ML_Final_project checkout"; exit 1; }
command -v git-filter-repo >/dev/null 2>&1 || { echo "need git-filter-repo:  pip install git-filter-repo"; exit 1; }

REMOTE="$(git remote get-url origin)"
echo "remote:  $REMOTE"
echo "branch:  $(git rev-parse --abbrev-ref HEAD)"
echo
echo "This rewrites ALL history and force-pushes. Every commit SHA changes."
read -rp "Type REWRITE to continue: " ok
[ "$ok" = "REWRITE" ] || exit 1

BACKUP="../ML_Final_project.backup.$(date +%Y%m%d-%H%M%S)"
echo "backing up the full repo to $BACKUP"
cp -R . "$BACKUP"

git filter-repo --force \
  --path images/ \
  --path food_recipe/ \
  --path graphify-out/ \
  --path anaconda_projects/ \
  --path outputs/recipe_metadata.csv \
  --path-glob '*.pptx' \
  --path-glob '*.ipynb.bak' \
  --invert-paths

# filter-repo drops the remote on purpose
git remote add origin "$REMOTE" 2>/dev/null || git remote set-url origin "$REMOTE"

echo
du -sh .git
echo
echo "verify the tree looks right, then:"
echo "  git push --force origin $(git rev-parse --abbrev-ref HEAD)"
echo
echo "backup kept at $BACKUP - delete it once you are happy"
