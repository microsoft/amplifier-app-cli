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

# --- PER-BATCH TMUX SOCKET (model_performance-ye80) -------------------------
# Default the socket to hw-<batch>, NOT a shared "hw".
#
# WHY: a tmux SERVER restart destroys every session on its socket. With every
# batch on one shared socket, any batch can annihilate every other batch's
# lanes AND their watchdogs in a single instant. Observed 2026-09-05: a second
# batch restarted the server on socket "hw" and killed three unrelated lanes
# plus this batch's watchdog at once -- no lane logged an error, because
# nothing in a lane went wrong. Six DTU containers were left RUNNING with open
# infra-ledger rows (Rule 14: nothing the highway stands up should outlive it).
#
# An explicitly exported HIGHWAY_TMUX_SOCKET still wins, so this is
# backward-compatible; only the DEFAULT changes.
HIGHWAY_TMUX_SOCKET="${HIGHWAY_TMUX_SOCKET:-hw-$(printf '%s' "$(basename "$BATCH_DIR")" | tr -c 'A-Za-z0-9_-' '_')}"
export HIGHWAY_TMUX_SOCKET
# ---------------------------------------------------------------------------

LANE=${2:?LANE required}
MANIFEST="$BATCH_DIR/manifest.tsv"

row=$(awk -F'\t' -v l="$LANE" 'NR>1 && $1==l' "$MANIFEST" || true)
[ -n "$row" ] || { echo "ERROR: lane '$LANE' not in manifest $MANIFEST" >&2; exit 1; }
IFS=$'\t' read -r lane wt branch base tmuxn goal log ts <<< "$row"

echo "== lane=$lane branch=$branch base=${base:0:8} wt=$wt"
if tmux -L "$HIGHWAY_TMUX_SOCKET" has-session -t "$tmuxn" 2>/dev/null; then
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

# TEST-EDIT FLAG: from the three-dot name list, always report how many touched
# paths look like tests, so the orchestrator can weigh a lane's own "I added
# tests" claim against git ground truth. A path counts as a test edit when it
# contains a tests?/ directory component, or a test_/_test filename marker.
names=$(git -C "$wt" diff --name-only "$base...HEAD" || true)
test_edits=""
if [ -n "$names" ]; then
  test_edits=$(printf '%s\n' "$names" | grep -E '(^|/)tests?/|test_|_test' || true)
fi
if [ -n "$test_edits" ]; then
  n=$(printf '%s\n' "$test_edits" | grep -c .)
  echo "TEST-EDITS: $n file(s)"
  printf '%s\n' "$test_edits" | sed 's/^/  /'
else
  echo "TEST-EDITS: 0 file(s)"
fi

echo "-- last 3 commits:"
git -C "$wt" log --oneline -3

echo "REMINDER: merge --no-ff from the MAIN checkout, ascending by churn; re-run the suite YOURSELF after EVERY merge."
