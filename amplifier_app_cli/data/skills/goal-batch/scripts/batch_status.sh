#!/usr/bin/env bash
# batch_status.sh <manifest.tsv> [base_sha]
#
# One line per lane. This is the instrument the watcher reads.
# Reads the 8-column manifest that launch_lane.sh writes. Never hand-write it.
#
# SHOW_LAST=1  also print each lane's last 3 top-level turns.
#
# --- what each signal is, and why NOT the obvious alternative ----------------
# TERMINAL  = DONE.json in the worktree root, verified against the manifest's
#             session_id when present. Pane death alone cannot distinguish
#             "goal satisfied" from "killed".
# ALIVE     = kill -0 on the lane PID. NOT tmux presence: the tmux server has
#             died mid-batch in three separate real runs, leaving healthy lanes
#             running as orphans while a tmux-keyed probe went blind.
# LIVENESS  = newest events.jsonl mtime in the lane's workspace. NOT
#             transcript.jsonl mtime, which freezes entirely while a lane
#             delegates to a sub-agent (measured: 4 live sessions, 124s apart,
#             zero transcript movement).
# PROGRESS  = assistant turns counted from the transcript. NOT
#             metadata.turn_count, which is corrupt (reports 1 for 21 turns).
# PUSHED    = git ls-remote. NOT @{upstream}, never set by explicit-refspec push.
#
# BATCH_ELAPSED comes from the manifest header, NOT from any lane's idle timer.
# A watcher once read a lane's idle seconds as batch elapsed time and killed the
# watch at 30 minutes claiming 95. The per-lane column is named LANE_IDLE so the
# two can never be confused again.
#
# Requires GNU find (-printf), jq, git, tmux.

set -uo pipefail

MAN="${1:?usage: batch_status.sh <manifest.tsv> [base_sha]}"
BASE_SHA="${2:-}"
STATE="${GOAL_BATCH_STATE:-/tmp/goal-batch-state}"
WARM="${GOAL_BATCH_WARM:-120}"    # idle < WARM -> WORKING
COLD="${GOAL_BATCH_COLD:-900}"    # idle < COLD -> SLOW, else STALLED
CI_BASE="${AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH:-$HOME/.amplifier/projects}"

[ -s "$MAN" ] || { echo "FATAL: manifest missing or empty: $MAN" >&2; exit 2; }
mkdir -p "$STATE"
NOW=$(date +%s)
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- batch-level elapsed, from the manifest header ---------------------------
LAUNCHED=$(sed -n 's/.*launched_at=\([0-9]*\).*/\1/p' "$MAN" | head -1)
if [ -n "${LAUNCHED:-}" ]; then
  E=$(( NOW - LAUNCHED ))
  printf 'BATCH_ELAPSED %02d:%02d:%02d   manifest=%s\n' $((E/3600)) $((E%3600/60)) $((E%60)) "$MAN"
else
  printf 'BATCH_ELAPSED unknown (manifest header has no launched_at)   manifest=%s\n' "$MAN"
fi

printf '%-14s %-19s %-10s %-10s %-8s %-6s %-7s %s\n' \
  LANE VERDICT LANE_IDLE TURNS COMMITS DIRTY PUSHED BRANCH

while IFS=$'\t' read -r lane wt branch tmuxname goal log sid pid; do
  case "${lane:-}" in ''|'#'*) continue ;; esac

  # --- terminal marker, guarded against a stale/inherited file ---------------
  done_state=no
  if [ -f "$wt/DONE.json" ]; then
    done_sid=$(jq -r '.session_id // empty' "$wt/DONE.json" 2>/dev/null)
    if [ -n "$done_sid" ] && [ -n "${sid:-}" ] && [ "$done_sid" != "$sid" ]; then
      done_state=stale
    else
      done_state=yes
    fi
  fi

  # --- verdict normalisation: the enum drifted across lanes in one real batch
  verdict_field=""
  if [ "$done_state" = yes ]; then
    verdict_field=$(jq -r '(.verdict // "") | ascii_upcase' "$wt/DONE.json" 2>/dev/null)
    case "$verdict_field" in
      COMPLETE|PASS|SUCCESS|OK|DONE) verdict_field=COMPLETE ;;
      BLOCKED)                       verdict_field=BLOCKED ;;
      PARTIAL)                       verdict_field=PARTIAL ;;
      "")                            verdict_field=COMPLETE ;;
      *)                             verdict_field="ODD:$verdict_field" ;;
    esac
  fi

  # --- liveness (stat only, never a read) -----------------------------------
  slug="$(printf '%s' "$wt" | tr '/' '-')"
  ws="$CI_BASE/$slug/sessions"
  newest=$(find "$ws" -maxdepth 2 -name events.jsonl -printf '%T@\n' 2>/dev/null | sort -rn | head -1)
  newest="${newest%%.*}"
  if [ -n "${newest:-}" ]; then idle=$(( NOW - newest )); idle_s="${idle}s"
  else                          idle=-1;                 idle_s="n/a"; fi

  # --- progress -------------------------------------------------------------
  turns='-'
  tr_file="$ws/${sid:-__none__}/transcript.jsonl"
  if [ -f "$tr_file" ]; then
    n=$(jq -r 'select(.role=="assistant")|1' "$tr_file" 2>/dev/null | wc -l | tr -d ' ')
    prev=$(cat "$STATE/${sid}.turns" 2>/dev/null || echo 0)
    printf '%s' "$n" > "$STATE/${sid}.turns"
    turns="${n}+$(( n - prev ))"
  fi

  # --- git facts ------------------------------------------------------------
  if [ -d "$wt" ]; then
    dirty=$(git -C "$wt" status --porcelain 2>/dev/null | wc -l | tr -d ' ')
    if [ -n "$BASE_SHA" ]; then
      commits=$(git -C "$wt" rev-list --count "${BASE_SHA}..HEAD" 2>/dev/null || echo '-')
    else
      commits=$(git -C "$wt" rev-list --count HEAD 2>/dev/null || echo '-')
    fi
    pushed=$(timeout 20 git -C "$wt" ls-remote --heads origin "$branch" 2>/dev/null | grep -c .)
  else
    dirty='-'; commits='-'; pushed='-'
  fi

  # --- alive: PID first, tmux only as corroboration -------------------------
  alive=no
  if [ -n "${pid:-}" ] && [ "$pid" != "-" ] && kill -0 "$pid" 2>/dev/null; then
    alive=yes
  elif tmux has-session -t "$tmuxname" 2>/dev/null; then
    alive=yes
  fi

  # --- verdict (precedence matters) -----------------------------------------
  if   [ ! -d "$wt" ];                                        then v=WORKTREE-MISSING
  elif [ "$done_state" = stale ];                             then v=STALE-DONE-IGNORED
  elif [ "$done_state" = yes ] && [ "$dirty" != 0 ];          then v="${verdict_field}-DIRTY"
  elif [ "$done_state" = yes ] && [ "$pushed" = 0 ];          then v="${verdict_field}-NOT-PUSHED"
  elif [ "$done_state" = yes ];                               then v="$verdict_field"
  elif [ "$alive" = yes ] && [ "$idle" -lt 0 ];               then v=STARTING
  elif [ "$alive" = yes ] && [ "$idle" -lt "$WARM" ];         then v=WORKING
  elif [ "$alive" = yes ] && [ "$idle" -lt "$COLD" ];         then v=SLOW
  elif [ "$alive" = yes ];                                    then v=STALLED
  elif [ "$idle" -lt 0 ];                                     then v=NOT-STARTED
  else                                                             v=DIED
  fi

  printf '%-14s %-19s %-10s %-10s %-8s %-6s %-7s %s\n' \
    "$lane" "$v" "$idle_s" "$turns" "$commits" "$dirty" "$pushed" "$branch"

  if [ "${SHOW_LAST:-0}" = 1 ] && [ -f "$tr_file" ]; then
    "$HERE/lane_turns.sh" "$tr_file" 3 200 | sed 's/^/    /'
  fi
done < "$MAN"
