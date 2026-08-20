#!/usr/bin/env bash
# highway_watchdog.sh — the loop that cannot be broken by conversation.
#
# Keeps watching lanes while the manager's turn is ended (e.g. it stopped to
# talk to the human). Wakes the orchestrator session when attention is needed.
#
# Run it DETACHED in its own tmux session (the launcher is the orchestrator):
#   tmux new-session -d -s "hw-watchdog__<batch>" \
#     "<skill_dir>/scripts/highway_watchdog.sh BATCH_DIR WIDTH SESSION_ID [INTERVAL] [MAX_HOURS]"
#
# Wake triggers: a lane ended since last poll | live < WIDTH | manager heartbeat stale.
# Wake path: `amplifier run --resume SESSION_ID "<wake prompt>"` (verified CLI flag),
# plus a durable `wake-needed` file in BATCH_DIR in case the resume fails.
# The orchestrator touches BATCH_DIR/.manager-heartbeat every cycle and deletes
# wake-needed entries it has processed.
set -uo pipefail   # deliberately NOT -e: the watch loop must survive transient failures

BATCH_DIR=${1:?BATCH_DIR required}
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
    tmux has-session -t "$tmuxn" 2>/dev/null && n=$((n+1))
  done < "$MANIFEST"
  echo "$n"
}

ended_list() {
  local lane wt branch base tmuxn rest
  while IFS=$'\t' read -r lane wt branch base tmuxn rest; do
    [ "$lane" = "lane" ] && continue
    tmux has-session -t "$tmuxn" 2>/dev/null || echo "$lane"
  done < "$MANIFEST"
}

wake() {
  local reason="$1" now
  now=$(date +%s)
  if [ $(( now - last_wake )) -lt "$WAKE_GAP" ]; then
    log "suppress wake (within ${WAKE_GAP}s gap): $reason"
    return 0
  fi
  printf '%s\t%s\n' "$(date -u +%FT%TZ)" "$reason" >> "$BATCH_DIR/wake-needed"
  log "WAKE: $reason"
  if amplifier run --resume "$SESSION_ID" --output-format json \
    "HIGHWAY WAKE (watchdog): $reason. Run one steady-state cycle now: highway_status.sh FIRST, verify+merge any landed lane, refill to width, update HIGHWAY.md and the todo lane board, touch the heartbeat, clear wake-needed. Do not end the turn while DEFICIT>0 or the watchdog is dead." \
    >> "$LOGF" 2>&1; then
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
  log "poll live=$live width=$WIDTH new_ended='${new_ended}' hb_age=${hb_age}s uptime=${wd_uptime}s"

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

  if [ -n "${new_ended// /}" ]; then wake "lane(s) ended: ${new_ended}"; continue; fi
  if [ "$live" -lt "$WIDTH" ]; then wake "under width: live=$live < width=$WIDTH"; continue; fi
  if [ "$hb_age" -gt "$HB_MAX" ] && [ "$live" -gt 0 ]; then
    wake "manager heartbeat stale (${hb_age}s) with $live live lanes"
  fi
done
