#!/usr/bin/env bash
# landed_from_git.sh — regenerate the "Landed" list from git ground truth, so
# HIGHWAY.md's Operating Picture can never drift from what actually merged
# (proof-run 01: HIGHWAY.md said "Landed: none" while 10 lanes had merged).
#
# Usage: landed_from_git.sh REPO [BASE_BRANCH]
# Emits markdown lines for the Landed section; paste into HIGHWAY.md each cycle.
set -euo pipefail
REPO=${1:?REPO required}
BASE=${2:-main}

n=$(git -C "$REPO" rev-list --count --merges "$BASE" 2>/dev/null || echo 0)
echo "## Landed (git ground truth: $n merges on $BASE)"
git -C "$REPO" log --merges --first-parent "$BASE" \
  --pretty=format:'- %h %s (%cI)' 2>/dev/null || true
echo
