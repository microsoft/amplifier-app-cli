#!/usr/bin/env bash
# highway_watchdog.sh — the loop that cannot be broken by conversation.
#
# Keeps watching lanes while the manager's turn is ended (e.g. it stopped to
# talk to the human). Wakes the orchestrator session when attention is needed.
#
# Run it DETACHED in its own tmux-session (the launcher is the orchestrator):
#   tmux -L "$HIGHWAY_TMUX_SOCKET" new-session -d -s "hw-watchdog__<batch>" \
#     "<skill_dir>/scripts/highway_watchdog.sh BATCH_DIR WIDTH SESSION_ID [INTERVAL] [MAX_HOURS]"
#
# Wake triggers: a lane ended since last poll | live < WIDTH | manager heartbeat stale.
# Wake path: `amplifier run --resume SESSION_ID "<wake prompt>"` (verified CLI flag),
# plus a durable `wake-needed` file in BATCH_DIR in case the resume fails.
# The orchestrator touches BATCH_DIR/.manager-heartbeat every cycle and deletes
# wake-needed entries it has processed.
set -uo pipefail   # deliberately NOT -e: the watch loop must survive transient failures

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
SESSION_ID=${3:?SESSION_ID required (the orchestrator session to wake)}
INTERVAL=${4:-300}
MAX_HOURS=${5:-12}

MANIFEST="$BATCH_DIR/manifest.tsv"
LOGF="$BATCH_DIR/watchdog.log"
STATE="$BATCH_DIR/.watchdog-state"
HB="$BATCH_DIR/.manager-heartbeat"
HB_MAX=${HIGHWAY_HEARTBEAT_MAX:-1800}
WAKE_GAP=${HIGHWAY_WAKE_GAP:-180}
# If the manager's heartbeat is fresher than this, its foreground turn is still
# active and will handle refill/merge inline. Waking then spawns a SECOND
# concurrent instance of the same session (observed race, fix-verify run) -- so
# defer every wake while the heartbeat is fresh.
ACTIVE_WINDOW=${HIGHWAY_ACTIVE_WINDOW:-120}
# stat -c %Y below is GNU/Linux; adjust for macOS if this ever travels.

# WIDTH PIN: BATCH_DIR/.width (a single integer) overrides the positional WIDTH.
# resolve_width re-reads it EVERY poll, so a mid-run width change is picked up
# live. width_source records which value is in force ('file' or 'arg').
WIDTH_ARG=$WIDTH
WIDTH_FILE="$BATCH_DIR/.width"
width_source=arg
resolve_width() {
  width_source=arg
  WIDTH=$WIDTH_ARG
  if [ -f "$WIDTH_FILE" ]; then
    local w
    w=$(head -n1 "$WIDTH_FILE" | tr -d '[:space:]')
    if printf '%s' "$w" | grep -qE '^[0-9]+$'; then
      WIDTH=$w
      width_source=file
    fi
  fi
}

# ESCALATION LADDER: after this many consecutive polls where we are under width
# AND live is not recovering (did not increase vs the previous poll), touch
# BATCH_DIR/escalation-needed and switch the wake prompt to the escalation form.
ESCALATE_AFTER=${HIGHWAY_ESCALATE_AFTER:-3}
prev_live=-1        # -1 so the very first poll never looks like a non-increase
ineffective=0       # consecutive ineffective (under-width, non-recovering) polls
escalate=0          # 1 while the ineffective count is at/over the threshold

deadline=$(( $(date +%s) + MAX_HOURS * 3600 ))
START_TS=$(date +%s)
# Grace after watchdog start during which an ABSENT heartbeat means "manager
# still warming up in Phase 4" (not "manager dead") -> defer waking. Closes the
# pre-first-heartbeat race window (graded trial 01).
GRACE=${HIGHWAY_HB_GRACE:-300}
last_wake=0

log() { echo "$(date -u +%FT%TZ) $*" >> "$LOGF"; }

live_lanes() {
  local n=0 lane wt branch base tmuxn rest
  while IFS=$'\t' read -r lane wt branch base tmuxn rest; do
    [ "$lane" = "lane" ] && continue
    tmux -L "$HIGHWAY_TMUX_SOCKET" has-session -t "$tmuxn" 2>/dev/null && n=$((n+1))
  done < "$MANIFEST"
  echo "$n"
}

ended_list() {
  local lane wt branch base tmuxn rest
  while IFS=$'\t' read -r lane wt branch base tmuxn rest; do
    [ "$lane" = "lane" ] && continue
    tmux -L "$HIGHWAY_TMUX_SOCKET" has-session -t "$tmuxn" 2>/dev/null || echo "$lane"
  done < "$MANIFEST"
}

wake() {
  local reason="$1" now prompt
  now=$(date +%s)
  if [ $(( now - last_wake )) -lt "$WAKE_GAP" ]; then
    log "suppress wake (within ${WAKE_GAP}s gap): $reason"
    return 0
  fi
  printf '%s\t%s\n' "$(date -u +%FT%TZ)" "$reason" >> "$BATCH_DIR/wake-needed"
  if [ "${escalate:-0}" = 1 ]; then
    # Escalation form: refill has stayed ineffective across multiple polls, so
    # the prompt leads with a distinct banner carrying the ineffective-wake
    # count, live, and width, and directs a root-cause look rather than a
    # reflexive relaunch.
    log "ESCALATION WAKE (ineffective=$ineffective live=$live width=$WIDTH): $reason"
    prompt="HIGHWAY ESCALATION (watchdog): refill has been ineffective for $ineffective consecutive poll(s) - live=$live is still under width=$WIDTH. $reason. Do NOT just relaunch blindly: run highway_status.sh FIRST, then find WHY lanes are not coming up to width (launch failures, exhausted ready queue, host/resource limits, stuck merges) and fix that, then refill. Do not end the turn while DEFICIT>0 or the watchdog is dead."
  else
    log "WAKE: $reason"
    prompt="HIGHWAY WAKE (watchdog): $reason. Run one steady-state cycle now: highway_status.sh FIRST, verify+merge any landed lane, refill to width, update HIGHWAY.md and the todo lane board, touch the heartbeat, clear wake-needed. Do not end the turn while DEFICIT>0 or the watchdog is dead."
  fi
  if amplifier run --resume "$SESSION_ID" --output-format json "$prompt" >> "$LOGF" 2>&1; then
    log "wake delivered (resume ok)"
    last_wake=$now
  else
    # Deliberately NOT updating last_wake on failure: a failed wake must not
    # suppress the retry on the next poll the way a successful one would.
    log "wake FAILED (resume rc=$?) - wake-needed file left as the durable signal; will retry next poll"
  fi
}

log "watchdog start batch=$BATCH_DIR width=$WIDTH session=$SESSION_ID interval=${INTERVAL}s max=${MAX_HOURS}h hb_max=${HB_MAX}s"
touch "$STATE"

while :; do
  sleep "$INTERVAL"

  if [ "$(date +%s)" -ge "$deadline" ]; then
    wake "watchdog max runtime (${MAX_HOURS}h) reached - restart me if the highway is still open"
    log "exit: deadline reached"
    exit 0
  fi

  [ -f "$MANIFEST" ] || { log "no manifest yet at $MANIFEST"; continue; }

  resolve_width   # WIDTH PIN: re-read BATCH_DIR/.width on every poll
  live=$(live_lanes)
  ended_now=$(ended_list | sort)
  ended_prev=$(sort "$STATE" 2>/dev/null || true)
  new_ended=$(comm -13 <(printf '%s\n' "$ended_prev") <(printf '%s\n' "$ended_now") | tr '\n' ' ')
  printf '%s\n' "$ended_now" > "$STATE"

  if [ -f "$HB" ]; then
    hb_age=$(( $(date +%s) - $(stat -c %Y "$HB") ))
  else
    hb_age=-1   # heartbeat not created yet
  fi
  wd_uptime=$(( $(date +%s) - START_TS ))
  log "poll live=$live width=$WIDTH width_source=$width_source new_ended='${new_ended}' hb_age=${hb_age}s uptime=${wd_uptime}s"

  # Manager's turn is active -> it handles refill/merge inline; do NOT wake (a
  # second concurrent --resume instance was the proof-run-02/trial-01 race).
  # Active = a FRESH heartbeat, OR no heartbeat yet but the watchdog only just
  # started (manager still in Phase 4, before its first touch).
  if { [ "$hb_age" -ge 0 ] && [ "$hb_age" -lt "$ACTIVE_WINDOW" ]; } \
     || { [ "$hb_age" -lt 0 ] && [ "$wd_uptime" -lt "$GRACE" ]; }; then
    log "manager active (hb_age=${hb_age}s, uptime=${wd_uptime}s) - deferring wake"
    continue
  fi
  # Heartbeat absent beyond the grace window = manager never checked in =
  # treat as stale so the safety-net wakes below still fire.
  [ "$hb_age" -lt 0 ] && hb_age=$(( HB_MAX + 1 ))

  # ESCALATION LADDER: count consecutive polls where live < width AND live did
  # not increase vs the previous poll; reset the moment live increases or the
  # deficit clears. Computed on non-deferred polls only (a deferred poll means
  # the manager is active and refilling inline, so it is not an ineffective wake).
  if [ "$live" -ge "$WIDTH" ]; then
    ineffective=0          # deficit cleared
  elif [ "$live" -gt "$prev_live" ]; then
    ineffective=0          # live increased since the previous poll
  else
    ineffective=$(( ineffective + 1 ))
  fi
  prev_live=$live
  escalate=0
  if [ "$ineffective" -ge "$ESCALATE_AFTER" ]; then
    escalate=1
    touch "$BATCH_DIR/escalation-needed"
    log "ESCALATION: ineffective=$ineffective >= ${ESCALATE_AFTER} (live=$live width=$WIDTH) - marker touched"
  fi

  if [ -n "${new_ended// /}" ]; then wake "lane(s) ended: ${new_ended}"; continue; fi
  if [ "$live" -lt "$WIDTH" ]; then wake "under width: live=$live < width=$WIDTH"; continue; fi
  if [ "$hb_age" -gt "$HB_MAX" ] && [ "$live" -gt 0 ]; then
    wake "manager heartbeat stale (${hb_age}s) with $live live lanes"
  fi
done
