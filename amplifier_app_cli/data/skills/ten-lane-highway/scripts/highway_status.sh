#!/usr/bin/env bash
# highway_status.sh — THE instrument. One call reports every lane AND computes the deficit.
#
# Usage: highway_status.sh BATCH_DIR WIDTH READY
#   WIDTH  target lane count (the number written in HIGHWAY.md)
#   READY  count of ready, unblocked work items (read it from the work queue and pass it)
#
# The deficit is COMPUTED HERE, BY CODE — never "noticed" by a model.
#   deficit = min(READY, max(0, WIDTH - live))
# If DEFICIT > 0 the correct next action is always: refill, before anything else.
set -euo pipefail

BATCH_DIR=${1:?BATCH_DIR required}
WIDTH=${2:?WIDTH required}
READY=${3:?READY required (ready unblocked work item count)}

MANIFEST="$BATCH_DIR/manifest.tsv"
STALL_SECS=${HIGHWAY_STALL_SECS:-900}
# HIGHWAY_JSON=1 -> emit ONE compact JSON line and nothing else (for the eval's
# M1 sampler daemon: `HIGHWAY_JSON=1 highway_status.sh ... >> deficit.jsonl`).
JSON=${HIGHWAY_JSON:-0}
# WIDTH PIN: if BATCH_DIR/.width holds a single integer, it overrides the
# positional WIDTH arg. width_source records which value won, so a reader can
# tell a pinned width from an argument-supplied one.
WIDTH_FILE="$BATCH_DIR/.width"
width_source=arg
if [ -f "$WIDTH_FILE" ]; then
  _wpin=$(head -n1 "$WIDTH_FILE" | tr -d '[:space:]')
  if printf '%s' "$_wpin" | grep -qE '^[0-9]+$'; then
    WIDTH=$_wpin
    width_source=file
  fi
fi
# Same sanitization as launch_lane.sh — the watchdog name must match byte-for-byte.
# (stat -c %Y below is GNU/Linux; adjust for macOS if this ever travels.)
BATCH=$(printf '%s' "$(basename "$BATCH_DIR")" | tr -c 'A-Za-z0-9_-' '_')
[ -f "$MANIFEST" ] || { echo "ERROR: no manifest at $MANIFEST (launch_lane.sh creates it)" >&2; exit 1; }

now=$(date +%s)
live=0; ended=0; done_n=0; stalled=0; gone=0

[ "$JSON" = 1 ] || printf '%-20s %-6s %-9s %-6s %-5s %-14s %s\n' LANE TMUX LOG_AGE AHEAD DONE FLAG WORKTREE
while IFS=$'\t' read -r lane wt branch base tmuxn goal log ts; do
  [ "$lane" = "lane" ] && continue

  if tmux -L "${HIGHWAY_TMUX_SOCKET:-hw}" has-session -t "$tmuxn" 2>/dev/null; then st=LIVE; else st=ENDED; fi

  age="-"; ahead="-"; dj="-"; wt_present=yes
  if [ -d "$wt" ]; then
    if [ -f "$log" ]; then age="$(( now - $(stat -c %Y "$log") ))s"; fi
    ahead=$(git -C "$wt" rev-list --count "$base..HEAD" 2>/dev/null || echo "?")
    if [ -f "$(dirname "$wt")/DONE.json" ]; then dj=yes; else dj=no; fi
  else
    wt_present=no
  fi

  flag=""
  if [ "$st" = "LIVE" ]; then
    # A live tmux-session counts as a live lane even if its worktree vanished —
    # that is an anomaly to flag, not a lane to ignore.
    live=$((live+1))
    if [ "$wt_present" = "no" ]; then flag="NO-WORKTREE"; fi
    if [ "$age" != "-" ] && [ "${age%s}" -gt "$STALL_SECS" ]; then flag="STALLED"; stalled=$((stalled+1)); fi
  else
    if [ "$wt_present" = "no" ]; then
      st="GONE"   # ended + worktree removed = normal post-teardown terminal state
      gone=$((gone+1))
    else
      ended=$((ended+1))
      if [ "$dj" = "no" ]; then flag="ENDED-NO-DONE"; fi
    fi
  fi
  if [ "$dj" = "yes" ]; then done_n=$((done_n+1)); fi

  [ "$JSON" = 1 ] || printf '%-20s %-6s %-9s %-6s %-5s %-14s %s\n' "$lane" "$st" "$age" "$ahead" "$dj" "$flag" "$wt"
done < "$MANIFEST"

WD="hw-watchdog__${BATCH}"
if tmux -L "${HIGHWAY_TMUX_SOCKET:-hw}" has-session -t "$WD" 2>/dev/null; then wd_st=LIVE; else wd_st=DEAD; fi

open=$(( WIDTH - live )); if [ "$open" -lt 0 ]; then open=0; fi
if [ "$READY" -lt "$open" ]; then deficit=$READY; else deficit=$open; fi

if [ "$JSON" = 1 ]; then
  printf '{"ts":"%s","batch":"%s","live":%d,"ended":%d,"done_marker":%d,"stalled":%d,"gone":%d,"width":%d,"width_source":"%s","ready":%d,"deficit":%d,"watchdog":"%s"}\n' \
    "$(date -u +%FT%TZ)" "$BATCH" "$live" "$ended" "$done_n" "$stalled" "$gone" "$WIDTH" "$width_source" "$READY" "$deficit" "$wd_st"
  exit 0
fi

echo
echo "SUMMARY batch=$BATCH live=$live ended=$ended done_marker=$done_n stalled=$stalled gone=$gone width=$WIDTH width_source=$width_source ready=$READY watchdog=$wd_st"
echo "DEFICIT=$deficit"
if [ "$deficit" -gt 0 ]; then
  echo "ACTION: launch $deficit lane(s) NOW - refill before anything else."
fi
if [ "$wd_st" = "DEAD" ]; then
  echo "WARNING: watchdog $WD is not running - do not end the turn until it is."
fi
