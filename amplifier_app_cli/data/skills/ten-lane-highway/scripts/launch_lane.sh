#!/usr/bin/env bash
# launch_lane.sh — start ONE highway lane: worktree + branch + tmux + autonomous /goal session.
#
# Usage: launch_lane.sh BATCH_DIR LANE REPO_PATH GOAL_FILE [BASE_REF]
#   BATCH_DIR  directory holding this highway's state (manifest, logs, lanes/)
#   LANE       kebab-case lane name (becomes branch lane/<LANE> and tmux hw__<batch>__<LANE>)
#   REPO_PATH  path to the repo this lane owns
#   GOAL_FILE  path to the goalify-composed goal file for this lane
#   BASE_REF   ref to branch from (default: main)
#
# This script is the ONLY writer of manifest.tsv. Never hand-write the manifest.
# Idempotent: re-running skips an existing worktree / running tmux session.
set -euo pipefail

BATCH_DIR=${1:?BATCH_DIR required}
LANE=${2:?LANE required}
REPO=${3:?REPO_PATH required}
GOAL=${4:?GOAL_FILE required}
BASE_REF=${5:-main}

# tmux treats '.' and ':' specially in -t targets: a name built from a raw
# basename may not round-trip through later has-session checks. Sanitize the
# batch identifier and REQUIRE clean lane names, so the name we create is
# byte-identical to the name every later check looks up.
BATCH=$(printf '%s' "$(basename "$BATCH_DIR")" | tr -c 'A-Za-z0-9_-' '_')
case "$LANE" in
  ''|*[!A-Za-z0-9_-]*) echo "ERROR: LANE must match [A-Za-z0-9_-]+ (got: '$LANE')" >&2; exit 1 ;;
esac
MANIFEST="$BATCH_DIR/manifest.tsv"
LANES_ROOT=${HIGHWAY_LANES_ROOT:-"$BATCH_DIR/lanes"}

REPO=$(cd "$REPO" && pwd)
[ -f "$GOAL" ] || { echo "ERROR: goal file not found: $GOAL" >&2; exit 1; }
GOAL=$(cd "$(dirname "$GOAL")" && pwd)/$(basename "$GOAL")

TMUX_NAME="hw__${BATCH}__${LANE}"
WT="$LANES_ROOT/$LANE/$(basename "$REPO")"
BRANCH="lane/$LANE"
# Resolved BEFORE the worktree-exists check on purpose: a vanished/renamed
# BASE_REF should fail loud even on an idempotent relaunch.
BASE_SHA=$(git -C "$REPO" rev-parse "$BASE_REF")
LANE_DIR="$LANES_ROOT/$LANE"
# Terminal marker AND the lane log live OUTSIDE the git worktree (in the lane
# dir), so a lane's own `git add -A` can never stage the marker and collide at
# merge time (proof-run 01), and the log survives worktree teardown for capture
# (graded trial 01).
DONE_MARKER="$LANE_DIR/DONE.json"
LOG="$LANE_DIR/lane.log"

mkdir -p "$BATCH_DIR" "$LANES_ROOT/$LANE"
[ -f "$MANIFEST" ] || printf 'lane\tworktree\tbranch\tbase_sha\ttmux\tgoal\tlog\tlaunched_at\n' > "$MANIFEST"

# Worktree — idempotent (skip-if-exists, never fail-if-exists)
if [ ! -d "$WT" ]; then
  git -C "$REPO" worktree add -b "$BRANCH" "$WT" "$BASE_SHA" 2>/dev/null \
    || git -C "$REPO" worktree add "$WT" "$BRANCH"
fi

# Purge inherited terminal markers (a stale marker is a false-positive machine)
rm -f "$DONE_MARKER" "$WT/DONE.json"

# Belt-and-suspenders: keep lane scaffolding out of git even if a lane tries.
EXCL="$(git -C "$WT" rev-parse --path-format=absolute --git-common-dir)/info/exclude"
for p in GOAL.md DONE.json lane.log; do
  grep -qxF "$p" "$EXCL" 2>/dev/null || echo "$p" >> "$EXCL"
done

# The lane's goal rides in the worktree root; append the marker instruction so
# the lane writes its terminal marker OUTSIDE the repo, never inside it.
cp "$GOAL" "$WT/GOAL.md"
cat >> "$WT/GOAL.md" <<EOF

---
LANE COMPLETION MARKER (highway): when the goal is met, write your terminal
marker to the ABSOLUTE path below — it is OUTSIDE this repo on purpose. Do NOT
create, stage, or commit any DONE.json inside this repository; it collides at
merge time.
  $DONE_MARKER
EOF

if tmux has-session -t "$TMUX_NAME" 2>/dev/null; then
  echo "SKIP: $TMUX_NAME already running"
else
  tmux new-session -d -s "$TMUX_NAME" -c "$WT" \
    "amplifier run '/goal @GOAL.md' 2>&1 | tee '$LOG'"
  echo "LAUNCHED: $TMUX_NAME -> $WT ($BRANCH @ ${BASE_SHA:0:8})"
fi

# Manifest row — append once (this script is the manifest's only writer)
if ! awk -F'\t' -v l="$LANE" 'NR>1 && $1==l{found=1} END{exit !found}' "$MANIFEST"; then
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$LANE" "$WT" "$BRANCH" "$BASE_SHA" "$TMUX_NAME" "$GOAL" "$LOG" "$(date -u +%FT%TZ)" >> "$MANIFEST"
fi
