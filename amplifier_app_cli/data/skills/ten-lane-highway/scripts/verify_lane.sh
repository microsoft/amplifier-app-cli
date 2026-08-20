#!/usr/bin/env bash
# verify_lane.sh — git-facts probe for ONE lane. Reports ground truth; never merges.
#
# Usage: verify_lane.sh BATCH_DIR LANE
#
# "I need ground truth from git and the filesystem, not from what any session
# said about itself." Lane self-reports (including DONE.json) are hints — the
# orchestrator merges only after ITS OWN check of these facts, and re-runs the
# suite itself after the merge.
set -euo pipefail

BATCH_DIR=${1:?BATCH_DIR required}
LANE=${2:?LANE required}
MANIFEST="$BATCH_DIR/manifest.tsv"

row=$(awk -F'\t' -v l="$LANE" 'NR>1 && $1==l' "$MANIFEST" || true)
[ -n "$row" ] || { echo "ERROR: lane '$LANE' not in manifest $MANIFEST" >&2; exit 1; }
IFS=$'\t' read -r lane wt branch base tmuxn goal log ts <<< "$row"

echo "== lane=$lane branch=$branch base=${base:0:8} wt=$wt"
if tmux has-session -t "$tmuxn" 2>/dev/null; then
  echo "TMUX: LIVE (still running - do NOT merge yet)"
else
  echo "TMUX: ended"
fi
if [ ! -d "$wt" ]; then echo "WORKTREE: GONE (already merged+removed, or never created)"; exit 0; fi

echo "-- DONE.json (lane dir, OUTSIDE the repo):"
lane_marker="$(dirname "$wt")/DONE.json"
if [ -f "$lane_marker" ]; then cat "$lane_marker"; else echo "(absent)"; fi

echo "-- uncommitted (would be LOST on worktree prune - flag loudly if non-empty):"
git -C "$wt" status --short | head -20

echo "-- commits ahead of base: $(git -C "$wt" rev-list --count "$base..HEAD")"
mb=$(git -C "$wt" merge-base "$base" HEAD)
echo "-- diffstat merge-base..HEAD (three-dot truth; two-dot lies after base moves):"
git -C "$wt" diff --stat "$mb..HEAD" | tail -15

echo "-- last 3 commits:"
git -C "$wt" log --oneline -3

echo "REMINDER: merge --no-ff from the MAIN checkout, ascending by churn; re-run the suite YOURSELF after EVERY merge."
