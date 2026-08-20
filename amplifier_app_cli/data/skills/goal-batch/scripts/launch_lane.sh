#!/usr/bin/env bash
# launch_lane.sh <batch> <lane> <worktree> <goal-rel-path> <manifest> [wall_s] [max_turns]
#
#   batch/lane     name the batch and the lane. tmux session and log path are
#                  DERIVED from them -- gb__<batch>__<lane> and
#                  /tmp/gb-<batch>-<lane>.log -- so the naming convention that
#                  the orphan sweep and pane-viewer match strings depend on
#                  cannot drift.
#   goal-rel-path  path RELATIVE TO THE WORKTREE. Must be committed to the base
#                  commit before `git worktree add`, so every worktree has it.
#   manifest       the batch manifest. THIS SCRIPT OWNS IT: it writes the header
#                  on first call and appends one row per lane. Nothing else
#                  should ever write it by hand.
#   wall_s         wall-clock bound, default 7200. 0 disables.
#   max_turns      turn bound passed to /goal, default 0 (unlimited, ADR-0005).
#                  NOTE: --max-turns is parsed by /goal, NOT by `amplifier run`,
#                  so it rides inside the prompt string.
#
# MANIFEST SCHEMA (8 columns, tab-separated) -- batch_status.sh reads exactly this:
#   lane  worktree  branch  tmux  goal  log  session_id  pid
#
# v2 changes, each bought with a measured failure in real team use:
#  - Owns the manifest end-to-end. v1 emitted 5 columns while batch_status.sh
#    read 7; the two never agreed, and every real user hand-wrote the manifest
#    to work around it.
#  - Records the lane PID. tmux servers have died mid-batch in three separate
#    runs; PID liveness keeps the status probe working when tmux is gone.
#  - Purges any inherited DONE.json before launch. A DONE.json carried in
#    through the base commit reads as an already-finished lane.
#  - `setsid --wait`. Bare `setsid` launched ZERO of five lanes on another
#    operator's machine while working fine here.

set -uo pipefail

BATCH="${1:?batch name}"; LANE="${2:?lane name}"; WT="${3:?worktree}"
GOAL="${4:?goal path relative to worktree}"; MANIFEST="${5:?manifest path}"
WALL="${6:-7200}"; MAXTURNS="${7:-0}"

TMUXNAME="gb__${BATCH}__${LANE}"
LOG="/tmp/gb-${BATCH}-${LANE}.log"
PIDFILE="/tmp/gb-${BATCH}-${LANE}.pid"

# --- preflight: fail loud, never launch degraded ------------------------------
[ -d "$WT" ] || { echo "FATAL $LANE: worktree missing: $WT" >&2; exit 2; }
[ -f "$WT/$GOAL" ] || {
  echo "FATAL $LANE: goal file not in worktree: $WT/$GOAL" >&2
  echo "  Goal files must be COMMITTED to the base commit before 'git worktree add'." >&2
  exit 2; }
case "$GOAL" in *[\'\"\ \$\`\\]*)
  echo "FATAL $LANE: goal path has shell metachars: $GOAL" >&2; exit 2 ;; esac
case "$BATCH$LANE" in *[!a-zA-Z0-9-]*)
  echo "FATAL: batch/lane must be [a-zA-Z0-9-] only (got '$BATCH'/'$LANE')" >&2; exit 2 ;; esac
case "$MAXTURNS" in ''|*[!0-9]*)
  echo "FATAL $LANE: max_turns must be a non-negative integer: $MAXTURNS" >&2; exit 2 ;; esac

if tmux has-session -t "$TMUXNAME" 2>/dev/null; then
  echo "SKIP $LANE: tmux session '$TMUXNAME' already live (not clobbering)"
  exit 3
fi

# The lane runs under a non-login shell inside tmux. `amplifier` living only on a
# login-shell PATH is the difference between a clean abort here and a 60-second
# wait for a Session ID that can never arrive.
AMP="$(command -v amplifier || true)"
if [ -z "$AMP" ]; then
  AMP="$(bash -lc 'command -v amplifier' 2>/dev/null || true)"
  [ -n "$AMP" ] || { echo "FATAL $LANE: 'amplifier' not on PATH (checked plain and login shell)." >&2; exit 2; }
  echo "NOTE $LANE: amplifier only on the login PATH; pinning $AMP" >&2
fi

# --- lane-environment preflight ----------------------------------------------
# A lane inherits the TMUX SERVER's environment, not this shell's. When a var
# the settings interpolate is missing there, it resolves EMPTY -- e.g.
# `base_url: ${ANTHROPIC_BASE_URL}` becomes "" and every LLM call dies with
# "Connection error" that looks exactly like a network outage. That killed three
# lanes twice in one day while the API was reachable the whole time. Refuse to
# launch rather than die mid-run.
# Vars are discovered from the settings files, not hardcoded -- a provider this
# script has never heard of is covered for free.
if tmux has-session 2>/dev/null || tmux ls >/dev/null 2>&1; then
  VARS=$(cat ~/.amplifier/settings.yaml .amplifier/settings.yaml 2>/dev/null \
         | grep -oE '\$\{[A-Z_][A-Z0-9_]*\}' | tr -d '${}' | sort -u)
  missing=""
  for v in $VARS; do
    here="$(printenv "$v" 2>/dev/null || true)"
    there="$(tmux show-environment -g "$v" 2>/dev/null | sed "s/^$v=//")"
    case "$there" in "-$v"|"") there="" ;; esac
    [ -n "$here" ] && [ -z "$there" ] && missing="$missing $v"
  done
  if [ -n "$missing" ]; then
    echo "FATAL $LANE: set in this shell but EMPTY in the tmux server env:$missing" >&2
    echo "  The lane would inherit empty values and fail with a misleading" >&2
    echo "  'Connection error' on its first LLM call. Fix, then relaunch:" >&2
    for v in $missing; do echo "    tmux set-environment -g $v \"\$$v\"" >&2; done
    exit 2
  fi
fi

BRANCH=$(git -C "$WT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')

# A DONE.json inherited from the base commit means "finished" to every reader.
rm -f "$WT/DONE.json" "$PIDFILE"

# /goal parses a LEADING --max-turns; 0 means unlimited, so omit it entirely.
if [ "$MAXTURNS" -gt 0 ]; then PROMPT="/goal --max-turns ${MAXTURNS} @${GOAL}"
else                           PROMPT="/goal @${GOAL}"; fi

# --- tmux guard: MECHANICAL, because knowledge failed twice -------------------
# A lane that runs `tmux kill-server` without -L/-S kills the server it is
# RUNNING IN. tmux resolves -S > -L > $TMUX > TMUX_TMPDIR, and $TMUX is set in
# every process descended from an attached client -- so TMUX_TMPDIR is NOT
# isolation. This has happened twice on this class of machine: 73 live sessions
# destroyed 2026-08-08, 81 more on 2026-08-12. The second lane had the
# precedence rule documented in its own context when it ran the command.
# Documentation does not stop this. A shim on the lane's PATH does.
# Lanes have no legitimate business on the shared server; isolated work must
# name its socket on EVERY call, including the teardown -- the teardown is the
# call that bites.
GUARD_DIR="/tmp/gb-tmux-guard"
REAL_TMUX="$(command -v tmux || true)"
if [ -n "$REAL_TMUX" ]; then
  mkdir -p "$GUARD_DIR"
  cat > "$GUARD_DIR/tmux" <<GUARD
#!/usr/bin/env bash
# Auto-installed by goal-batch launch_lane.sh. Applies to LANES only.
for a in "\$@"; do
  case "\$a" in -L|-S|-L*|-S*) exec "$REAL_TMUX" "\$@" ;; esac
done
echo "[gb-tmux-guard] REFUSED: tmux invoked without an explicit -L <name> or -S <path>." >&2
echo "[gb-tmux-guard] TMUX_TMPDIR is NOT isolation -- \\\$TMUX outranks it. That exact" >&2
echo "[gb-tmux-guard] mistake destroyed 73 live sessions on 2026-08-08 and 81 on 08-12." >&2
echo "[gb-tmux-guard] Use an isolated socket and pass -L <unique-name> on EVERY call," >&2
echo "[gb-tmux-guard] including teardown. You are a lane; you do not need the shared server." >&2
exit 78
GUARD
  chmod +x "$GUARD_DIR/tmux"
fi

# Wrapper keeps quoting off the tmux command line and owns process-group cleanup
# that plain `timeout` does not do (it signals only its direct child).
WRAP="$(mktemp "/tmp/gb-wrap-${BATCH}-${LANE}-XXXXXX.sh")"
{
  echo '#!/usr/bin/env bash'
  [ -n "$REAL_TMUX" ] && printf 'export PATH=%q:"$PATH"\n' "$GUARD_DIR"
  printf 'echo $$ > %q\n' "$PIDFILE"
  echo 'trap '\''kill -- -$$ 2>/dev/null'\'' EXIT'
  if [ "$WALL" -gt 0 ]; then
    printf 'timeout --signal=TERM --kill-after=60s %ss %q run %q 2>&1 | tee %q\n' \
           "$WALL" "$AMP" "$PROMPT" "$LOG"
  else
    printf '%q run %q 2>&1 | tee %q\n' "$AMP" "$PROMPT" "$LOG"
  fi
  printf 'echo "LANE_EXIT=${PIPESTATUS[0]} $(date -Is)" >> %q\n' "$LOG"
} > "$WRAP"
chmod +x "$WRAP"

tmux new-session -d -s "$TMUXNAME" -c "$WT" "setsid --wait $WRAP"

# --- harvest the session ID; FAIL LOUD if it never appears --------------------
# The manifest is the crash anchor. A blank ID in it is worse than no manifest,
# because recovery will trust it.
SID=""
for _ in $(seq 1 24); do            # up to ~60s for a cold start
  sleep 2.5
  SID=$(grep -am1 'Session ID:' "$LOG" 2>/dev/null | sed 's/.*Session ID: *//' | tr -d '\r')
  [ -n "$SID" ] && break
  tmux has-session -t "$TMUXNAME" 2>/dev/null || break   # died during startup
done

if [ -z "$SID" ]; then
  alive=$(tmux has-session -t "$TMUXNAME" 2>/dev/null && echo yes || echo no)
  echo "FATAL $LANE: no Session ID after 60s (session alive=$alive). Log tail:" >&2
  tail -5 "$LOG" 2>/dev/null >&2
  exit 4
fi

PID=$(cat "$PIDFILE" 2>/dev/null || echo '-')

# --- write the manifest (header once, then one row per lane) ------------------
if [ ! -s "$MANIFEST" ]; then
  mkdir -p "$(dirname "$MANIFEST")"
  {
    printf '# lane\tworktree\tbranch\ttmux\tgoal\tlog\tsession_id\tpid\n'
    printf '# batch=%s launched_at=%s wall_s=%s max_turns=%s\n' \
           "$BATCH" "$(date +%s)" "$WALL" "$MAXTURNS"
  } > "$MANIFEST"
fi
printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
  "$LANE" "$WT" "$BRANCH" "$TMUXNAME" "$GOAL" "$LOG" "$SID" "$PID" | tee -a "$MANIFEST"
