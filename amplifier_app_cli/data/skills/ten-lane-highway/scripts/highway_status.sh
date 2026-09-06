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
# Lane-name sets for the ORPHAN-ROWS join below (newline-joined, awk splits them).
live_lanes=""; all_lanes=""

[ "$JSON" = 1 ] || printf '%-20s %-6s %-9s %-6s %-5s %-14s %s\n' LANE TMUX LOG_AGE AHEAD DONE FLAG WORKTREE
while IFS=$'\t' read -r lane wt branch base tmuxn goal log ts; do
  [ "$lane" = "lane" ] && continue

  if tmux -L "$HIGHWAY_TMUX_SOCKET" has-session -t "$tmuxn" 2>/dev/null; then st=LIVE; else st=ENDED; fi

  age="-"; ahead="-"; dj="-"; wt_present=yes
  if [ -d "$wt" ]; then
    if [ -f "$log" ]; then age="$(( now - $(stat -c %Y "$log") ))s"; fi
    ahead=$(git -C "$wt" rev-list --count "$base..HEAD" 2>/dev/null || echo "?")
    if [ -f "$(dirname "$wt")/DONE.json" ]; then dj=yes; else dj=no; fi
  else
    wt_present=no
  fi

  all_lanes="$all_lanes$lane"$'\n'

  flag=""
  if [ "$st" = "LIVE" ]; then
    # A live tmux-session counts as a live lane even if its worktree vanished —
    # that is an anomaly to flag, not a lane to ignore.
    live=$((live+1))
    live_lanes="$live_lanes$lane"$'\n'
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
if tmux -L "$HIGHWAY_TMUX_SOCKET" has-session -t "$WD" 2>/dev/null; then wd_st=LIVE; else wd_st=DEAD; fi

# --- ORPHAN ROWS (model_performance-ye80) -----------------------------------
# Join lane liveness (above) to infra-ledger row ownership. Nothing else does.
#
# WHY: on 2026-09-05 a tmux server restart killed three lanes at once and left
# SIX DTU containers RUNNING with open ledger rows. Every part was working: the
# ledger recorded all six correctly, and this script reported the lanes ENDED.
# The two were simply never joined, so nobody could see "infrastructure with
# nothing driving it" (Rule 14) without running `incus list` by hand -- which is
# how the manager eventually found them.
#
# REPORTING ONLY. This block destroys nothing, writes nothing, and touches no
# ledger row. Reaping is deliberately NOT done here: a false positive that
# merely prints is a nuisance, a false positive that destroys is another 0rg.
#
# A LIVE lane's rows are NEVER counted. That is the load-bearing property --
# the reason this is a `live[]` lookup and not a "did the lane finish" guess.
#
# OWNER-NAME RESOLUTION, and why it is not a string equality. The two sides of
# this join disagree about what a lane is called, measured in this batch's own
# files: infra.owners.tsv holds SHORT work-item ids for 5 of 13 owners
# (127, 3d2, 6da, k64, vbs) and LONG manifest names for the other 8
# (drbf-compaction-notice-ab, ...). lane_teardown.sh documents the short form;
# lanes claim with whichever they have. So an exact-match join would have
# reported every short-id owner's rows as orphaned -- a false alarm on ~40% of
# owners, which is how a report gets ignored. Resolution is therefore
# exact-name, else a UNIQUE token-boundary prefix (`vbs` -> `vbs-...`), and
# ambiguity is reported as ambiguity rather than guessed at.
LEDGER_F="$BATCH_DIR/infra.tsv"
OWNERS_F="$BATCH_DIR/infra.owners.tsv"
orphan_rows=0
orphan_owners=""
if [ -f "$LEDGER_F" ]; then
  orphan_line=$(awk -F'\t' \
    -v owners="$OWNERS_F" -v live_s="$live_lanes" -v all_s="$all_lanes" '
    function resolve(o,   l, hit, cnt) {
      if (o == "") return ""
      if (o in all) return o
      cnt = 0
      for (l in all) if (index(l, o "-") == 1) { hit = l; cnt++ }
      if (cnt == 1) return hit
      if (cnt > 1)  return "\001ambiguous"
      return ""
    }
    BEGIN {
      n = split(live_s, t, "\n"); for (i = 1; i <= n; i++) if (t[i] != "") live[t[i]] = 1
      n = split(all_s,  t, "\n"); for (i = 1; i <= n; i++) if (t[i] != "") all[t[i]]  = 1
      while ((getline line < owners) > 0) {
        split(line, f, "\t"); if (f[2] != "") own[f[2]] = f[3]
      }
      k = 0; total = 0
    }
    $4 == "open" && $3 != "" {
      id = $3
      owner = (id in own) ? own[id] : ""
      r = resolve(owner)
      # A live lane owns it -> driven, not orphaned. The safety property.
      if (r != "" && r != "\001ambiguous" && (r in live)) next
      if      (owner == "")          key = "<unclaimed>"
      else if (r == "\001ambiguous") key = owner "(ambiguous-lane)"
      else if (r == "")              key = owner "(no-such-lane)"
      else                           key = r
      total++
      if (!(key in cnt)) order[++k] = key
      cnt[key]++
    }
    END {
      s = ""
      for (i = 1; i <= k; i++) s = s (s == "" ? "" : ",") order[i] "(" cnt[order[i]] ")"
      printf "%d\t%s", total, s
    }
  ' "$LEDGER_F")
  orphan_rows=${orphan_line%%$'\t'*}
  orphan_owners=${orphan_line#*$'\t'}
fi
# ---------------------------------------------------------------------------

open=$(( WIDTH - live )); if [ "$open" -lt 0 ]; then open=0; fi
if [ "$READY" -lt "$open" ]; then deficit=$READY; else deficit=$open; fi

if [ "$JSON" = 1 ]; then
  # The owner list is ledger-derived text landing inside a JSON string; keep it
  # to characters that cannot terminate one.
  oo=$(printf '%s' "$orphan_owners" | tr -c 'A-Za-z0-9_,()<>.:-' '_')
  printf '{"ts":"%s","batch":"%s","live":%d,"ended":%d,"done_marker":%d,"stalled":%d,"gone":%d,"width":%d,"width_source":"%s","ready":%d,"deficit":%d,"watchdog":"%s","orphan_rows":%d,"orphan_owners":"%s"}\n' \
    "$(date -u +%FT%TZ)" "$BATCH" "$live" "$ended" "$done_n" "$stalled" "$gone" "$WIDTH" "$width_source" "$READY" "$deficit" "$wd_st" "$orphan_rows" "$oo"
  exit 0
fi

echo
echo "SUMMARY batch=$BATCH live=$live ended=$ended done_marker=$done_n stalled=$stalled gone=$gone width=$WIDTH width_source=$width_source ready=$READY watchdog=$wd_st orphan_rows=$orphan_rows"
echo "DEFICIT=$deficit"
if [ "$deficit" -gt 0 ]; then
  echo "ACTION: launch $deficit lane(s) NOW - refill before anything else."
fi
if [ "$wd_st" = "DEAD" ]; then
  echo "WARNING: watchdog $WD is not running - do not end the turn until it is."
fi
if [ "$orphan_rows" -gt 0 ]; then
  # The count alone is not actionable: the manager needs the lane name to pass
  # to the lane-scoped teardown tool.
  echo "WARNING: orphan_rows=$orphan_rows owned by: $orphan_owners"
  echo "  Open infra-ledger rows whose owning lane is NOT live - infrastructure"
  echo "  with nothing driving it (Rule 14). Nothing was destroyed; reclaim each"
  echo "  with: lane_teardown.sh $BATCH_DIR <lane> teardown --yes"
fi
