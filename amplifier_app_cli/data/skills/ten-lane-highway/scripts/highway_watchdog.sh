#!/usr/bin/env bash
# highway_watchdog.sh — the loop that cannot be broken by conversation.
#
# Keeps watching lanes while the manager's turn is ended (e.g. it stopped to
# talk to the human). Wakes the orchestrator session when attention is needed.
#
# Run it DETACHED in its own tmux-session (the launcher is the orchestrator):
#   tmux -L "${HIGHWAY_TMUX_SOCKET:-hw}" new-session -d -s "hw-watchdog__<batch>" \
#     "<skill_dir>/scripts/highway_watchdog.sh BATCH_DIR WIDTH SESSION_ID [INTERVAL] [MAX_HOURS]"
#
# Wake triggers: a lane ended since last poll | live < WIDTH | manager heartbeat stale.
# Wake path: `amplifier run --resume SESSION_ID "<wake prompt>"` (verified CLI flag),
# plus a durable `wake-needed` file in BATCH_DIR in case the resume fails.
# The orchestrator touches BATCH_DIR/.manager-heartbeat every cycle and deletes
# wake-needed entries it has processed.
#
# AT THE RUNTIME CAP the loop does NOT simply exit (model_performance-6c3y):
#   batch still open  -> re-exec itself with the same arguments (CAP RE-ARM GUARD)
#   batch not open    -> deliver a final notice OUT OF BAND and exit (the cap's
#                        original purpose -- no orphan outliving its batch)
# It also touches BATCH_DIR/.watchdog-heartbeat every poll (SUPERVISION
# HEARTBEAT), so highway_status.sh can report not just THAT supervision lapsed
# but for HOW LONG, without anyone inferring it from a wake that never came.
set -uo pipefail   # deliberately NOT -e: the watch loop must survive transient failures
# A FAILED `exec` kills a non-interactive bash outright -- which would turn the
# re-arm below into the silent stop this whole guard exists to remove. execfail
# makes exec return instead, so the fallback path can run. (Caught by
# test_cap_rearms_even_when_the_exec_bit_is_stripped, not by reading the manual.)
shopt -s execfail

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
# SUPERVISION HEARTBEAT: proof-of-life this process writes for the MANAGER to
# read. The manager touches $HB so the watchdog can tell it is alive; until now
# nothing ran the other way, so "the watchdog stopped" was only ever visible to
# whoever happened to run highway_status.sh.
WD_HB="$BATCH_DIR/.watchdog-heartbeat"
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

# CAP: MAX_HOURS is the documented knob. HIGHWAY_MAX_SECONDS overrides it with a
# second-resolution cap -- the escape hatch for short-lived batches and for the
# tests, which cannot wait 12h to observe what happens at the boundary.
MAX_SECONDS=${HIGHWAY_MAX_SECONDS:-$(( MAX_HOURS * 3600 ))}
deadline=$(( $(date +%s) + MAX_SECONDS ))
# Which run of this process we are on. Bumped across every re-exec, so the log
# reads as one continuous supervision record rather than N unexplained starts.
GENERATION=${HIGHWAY_WATCHDOG_GENERATION:-1}
# CAP RE-ARM GUARD: "still open" = the lanes directory exists AND the manager
# has checked in within HIGHWAY_ABANDON_MAX. Deliberately much looser than
# HB_MAX (a manager idle for 30 min is normal and gets woken; one that has not
# touched its heartbeat in 6h has gone home). Re-arming ONLY while both hold is
# what preserves the cap's original purpose -- an orphaned watchdog can now
# outlive its batch by at most one cap period, never indefinitely.
LANES_DIR="$BATCH_DIR/lanes"
ABANDON_MAX=${HIGHWAY_ABANDON_MAX:-21600}
batch_state=""
batch_open() {
  if [ ! -d "$LANES_DIR" ]; then
    batch_state="no lanes dir at $LANES_DIR"; return 1
  fi
  if [ ! -f "$HB" ]; then
    batch_state="manager heartbeat $HB never created"; return 1
  fi
  local age
  age=$(( $(date +%s) - $(stat -c %Y "$HB") ))
  if [ "$age" -gt "$ABANDON_MAX" ]; then
    batch_state="manager heartbeat ancient (${age}s > ${ABANDON_MAX}s)"; return 1
  fi
  batch_state="lanes dir present, manager heartbeat ${age}s old"
  return 0
}
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
    tmux -L "${HIGHWAY_TMUX_SOCKET:-hw}" has-session -t "$tmuxn" 2>/dev/null && n=$((n+1))
  done < "$MANIFEST"
  echo "$n"
}

ended_list() {
  local lane wt branch base tmuxn rest
  while IFS=$'\t' read -r lane wt branch base tmuxn rest; do
    [ "$lane" = "lane" ] && continue
    tmux -L "${HIGHWAY_TMUX_SOCKET:-hw}" has-session -t "$tmuxn" 2>/dev/null || echo "$lane"
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

# The death notice. NOT wake(): it must never be suppressed by WAKE_GAP (the
# one message that matters most is the one most likely to land inside the gap),
# and it must NOT run as a child of this process inside this process's own tmux
# session. On 2026-09-03 it did: the resumed manager was told "restart me",
# reasonably ran `tmux kill-session -t hw-watchdog__<batch>` -- and killed
# ITSELF, because it was running inside that very session. It never reached the
# line that starts the replacement, and the highway sat idle ~34h. So the notice
# is dispatched detached (setsid/nohup), this process exits immediately, and the
# prompt says outright that killing the session is unnecessary.
final_notice() {
  local reason="$1" batch wd prompt body
  batch=$(printf '%s' "$(basename "$BATCH_DIR")" | tr -c 'A-Za-z0-9_-' '_')
  wd="hw-watchdog__${batch}"
  # Durable signal first: a file survives every delivery failure below.
  printf '%s\t%s\n' "$(date -u +%FT%TZ)" "$reason" >> "$BATCH_DIR/wake-needed"
  prompt="HIGHWAY WATCHDOG STOPPED (watchdog): $reason. Supervision of $BATCH_DIR has ENDED - no further wakes will arrive from me. If the highway is still open, start a fresh watchdog (Phase 4 command; session id in $BATCH_DIR/.session-id) and re-check highway_status.sh. The old watchdog process has ALREADY exited: do NOT run 'tmux kill-session -t $wd' first - on 2026-09-03 a responder did exactly that while running inside that session, killed itself mid-restart, and the highway sat idle ~34h."
  body='if amplifier run --resume "$1" --output-format json "$2" >> "$3" 2>&1; then
          printf "%s final notice delivered (resume ok)\n" "$(date -u +%FT%TZ)" >> "$3"
        else
          printf "%s final notice FAILED (resume rc=%s) - wake-needed is the durable signal\n" "$(date -u +%FT%TZ)" "$?" >> "$3"
        fi'
  if command -v setsid >/dev/null 2>&1; then
    setsid bash -c "$body" _ "$SESSION_ID" "$prompt" "$LOGF" >/dev/null 2>&1 &
    log "final notice dispatched detached (setsid pid=$!): $reason"
  else
    nohup bash -c "$body" _ "$SESSION_ID" "$prompt" "$LOGF" >/dev/null 2>&1 &
    log "final notice dispatched detached (nohup pid=$!): $reason"
  fi
}

log "watchdog start batch=$BATCH_DIR width=$WIDTH session=$SESSION_ID interval=${INTERVAL}s max=${MAX_SECONDS}s generation=$GENERATION hb_max=${HB_MAX}s"
touch "$STATE"
touch "$WD_HB"

while :; do
  sleep "$INTERVAL"
  touch "$WD_HB"   # SUPERVISION HEARTBEAT: written every poll, read by highway_status.sh

  if [ "$(date +%s)" -ge "$deadline" ]; then
    if batch_open; then
      # CAP RE-ARM GUARD: supervision continues past the cap while the batch is
      # open. No wake, no wake-needed line -- a re-arm costs the manager nothing
      # and needs nothing from it. The re-exec resets the deadline.
      log "CAP RE-ARM: cap (${MAX_SECONDS}s) reached but batch is still open ($batch_state) - re-exec as generation $(( GENERATION + 1 ))"
      export HIGHWAY_WATCHDOG_GENERATION=$(( GENERATION + 1 ))
      exec "$0" "$@"
      # Reached only if that exec failed (exec bit stripped by a packaging step,
      # noexec mount). Re-exec through the interpreter already running us before
      # giving up -- a re-arm that silently degrades to an exit is the very
      # failure this item exists to close.
      log "CAP RE-ARM: exec '$0' failed - retrying through ${BASH:-bash}"
      exec "${BASH:-bash}" "$0" "$@"
      log "CAP RE-ARM FAILED: could not re-exec '$0' - falling back to the wind-down path"
    fi
    final_notice "watchdog max runtime (${MAX_SECONDS}s, generation $GENERATION) reached and the batch is not open ($batch_state) - supervision has stopped"
    log "exit: deadline reached, batch not open ($batch_state)"
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
