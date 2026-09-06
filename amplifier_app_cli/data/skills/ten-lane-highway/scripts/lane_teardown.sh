#!/usr/bin/env bash
# lane_teardown.sh — LANE-SCOPED teardown for the ten-lane-highway infra ledger.
#
# WHERE THIS LIVES (item model_performance-giwq, 2026-09-06)
#   THIS FILE, shipped beside infra_ledger.sh in the ten-lane-highway skill, is
#   the ONE implementation. It used to live only in the evals repo, untracked
#   even there, while `infra_ledger.sh`'s own refusal message and SKILL.md sent
#   operators to it by name — so the skill's documented emergency recovery path
#   pointed at a file the skill did not ship. Resolve it the way every other
#   companion script resolves: `<skill_directory>/scripts/lane_teardown.sh`.
#
# WHY THIS EXISTS
#   The shipped skill helper `infra_ledger.sh BATCH_DIR sweep` is BATCH-GLOBAL:
#   its only predicate is `[ "$status" = "open" ]`, so the first lane to finish
#   destroys every other lane's live, mid-measurement containers. Observed
#   2026-09-02: one foreign sweep took lane l1's three DTUs and lane 161's three
#   DTUs (~$6.24 of in-flight paid work lost its in-container session trees).
#   That is why `sweep` now refuses (exit 3, having run nothing) when the open
#   rows span more than one owner — see infra_ledger.sh's MULTI-LANE GUARD.
#
#   This script tears down ONLY the rows a named lane owns, and structurally
#   refuses to run any other lane's destroy_cmd. There is deliberately NO
#   --all / --everything escape hatch: that is `sweep`, and `sweep` is the bug.
#
#   SECOND BUG, fixed here (item model_performance-1p4, 2026-09-02): the ledger
#   itself lies. Rows record `amplifier-digital-twin destroy <id> --force`, but
#   the CLI HAS NO --force OPTION (`destroy --help` lists only `--help`); such a
#   command exits rc=2 "No such option '--force'" WITHOUT TOUCHING the container,
#   so a status-flip keyed on rc can never reclaim those rows. The mirror-image
#   lie also exists: rows recorded with `|| true` force rc=0, so they get marked
#   swept whether or not the container died.
#
#   The fix is to stop trusting the recorded string in BOTH directions:
#     * the row's `id` column is authoritative, not the recorded command text;
#     * for DTU-managed rows the destroy is REBUILT canonically from that id,
#       so unknown flags, redirections and `|| true` are ignored, not executed;
#     * a row is NEVER flipped out of `open` on rc alone — the container must be
#       PROBED ABSENT afterwards. rc is evidence; the probe is proof.
#
# USAGE
#   lane_teardown.sh BATCH_DIR LANE list
#   lane_teardown.sh BATCH_DIR LANE claim ID [ID...]
#   lane_teardown.sh BATCH_DIR LANE teardown  [--yes] [--strict]
#   lane_teardown.sh BATCH_DIR LANE reconcile [--yes] [--strict]
#   lane_teardown.sh BATCH_DIR LANE audit     [--open-only]
#
#   BATCH_DIR   highway batch dir holding infra.tsv (e.g. /home/.../hw-model-performance)
#   LANE        SHORT lane / work-item id — e.g. woe, 161, dt1 — NOT the lane
#               directory name (`woe-lane-scoped-teardown`).
#
#   list        Classify every open ledger row for LANE. Read-only. Always exit 0
#               (exit 2 only if the ledger itself is unreadable).
#   claim       Record `id -> LANE` in BATCH_DIR/infra.owners.tsv. Do this at
#               creation, right after `infra_ledger.sh ... add`. Refuses to steal
#               an id another lane has already claimed.
#   teardown    DRY-RUN BY DEFAULT — prints what it *would* destroy and exits 0
#               without touching anything. `--yes` destroys this lane's rows and
#               flips ONLY the ones then PROBED ABSENT.
#   reconcile   Repair path for STALE-OPEN rows (row says open, container is
#               already gone — e.g. a lane destroyed by hand, or the row's
#               recorded command was unrunnable). Probes each of THIS LANE's open
#               rows and flips ONLY the absent ones. RUNS NO DESTROY COMMAND AT
#               ALL, so it can never kill anything. DRY-RUN BY DEFAULT.
#   audit       Read-only, batch-wide, mutates NOTHING and destroys NOTHING.
#               Classifies every row of every lane: unrunnable command, rc-masked
#               command, stale-open, live-open, and LEAKED (marked swept but the
#               container is still alive). Safe to run while other lanes are live.
#   --strict    Claims only: ignore convention-inferred ownership entirely.
#   --open-only audit: skip the swept rows (no LEAKED detection).
#
# EXIT CODES
#   0  did what was asked (including: this lane genuinely owns no open rows)
#   1  usage error, or a selected row is still open after teardown
#   2  the ledger exists but is unreadable
#   4  NEAR MISS — this lane owns no open rows, but open rows ARE held under a
#      near-miss lane name. Refuses rather than reporting success for a teardown
#      that would do nothing; names each candidate owner and its open-row count.
#
# OWNERSHIP MODEL (two sources, claims win)
#   1. CLAIMED  — BATCH_DIR/infra.owners.tsv maps id -> lane. Authoritative.
#   2. INFERRED — no claim for that id, and the id is exactly `val-<LANE>` or
#      starts with `val-<LANE>-`. Token-boundary only, so lane `l1` never
#      matches `val-l1b-stock`. Disabled automatically when LANE contains `-`
#      (then `l1` vs `l1-knob` would be ambiguous) and under --strict.
#   Everything else — claimed by another lane, or unattributable — is PROTECTED
#   and is never selected, never destroyed, and left byte-identical in the ledger.
#
# VERIFICATION MODEL (why a row leaves `open`)
#   DTU-managed rows (type dtu|container|dt|twin) are probed with
#   `amplifier-digital-twin status <id>`:
#     rc=0                      -> PRESENT
#     rc!=0 and "not found"     -> ABSENT
#     anything else             -> UNKNOWN
#   A row is flipped to `swept` ONLY on ABSENT. UNKNOWN fails CLOSED: the row
#   stays open. Non-DTU rows cannot be probed; they keep the legacy rc=0 rule and
#   are reported explicitly as VERIFY=unverifiable so the weaker guarantee is
#   never invisible.
#
# NOT -e: a failing destroy_cmd is an expected, handled outcome (matching
# upstream) — it must not abort the rest of the teardown.
set -uo pipefail

# Print the whole leading comment block. Derived from the file rather than a
# hardcoded line range: the range was '2,72p' and had already drifted past the
# end of the header, so every header edit silently truncated or over-ran the
# help text.
usage() { sed -n '2,/^set -uo pipefail/p' "$0" | sed '$d' >&2; exit 1; }

[ "$#" -ge 3 ] || usage
BATCH_DIR=${1:?BATCH_DIR required}
LANE=${2:?LANE required}
CMD=${3:?command required (list|claim|teardown|reconcile|audit)}
shift 3

LEDGER="$BATCH_DIR/infra.tsv"
OWNERS="$BATCH_DIR/infra.owners.tsv"
LOCK="$BATCH_DIR/.infra.lock"

# The DTU CLI is indirected so the test-suite can substitute a stub. Never
# defaulted to anything but the real tool.
DT_CLI=${AMPLIFIER_DT_CLI:-amplifier-digital-twin}

# Lane ids are compared as literal strings and used to build a prefix; keep them
# boring so neither comparison nor prefix can be surprising.
if [[ ! "$LANE" =~ ^[A-Za-z0-9][A-Za-z0-9_-]*$ ]]; then
  echo "ERROR: LANE '$LANE' must match ^[A-Za-z0-9][A-Za-z0-9_-]*$" >&2; exit 1
fi
# Ambiguity guard: with a dash in the lane id, `val-l1-knob-a` cannot be
# attributed to lane `l1` vs lane `l1-knob` by convention. Refuse to guess.
INFER=1
case "$LANE" in *-*) INFER=0 ;; esac
STRICT=0
ASSUME_YES=0
OPEN_ONLY=0
# Options belong to list/teardown/reconcile/audit; `claim`'s trailing args are IDs.
if [ "$CMD" != "claim" ]; then
  for arg in "$@"; do
    case "$arg" in
      --yes)       ASSUME_YES=1 ;;
      --dry-run)   ASSUME_YES=0 ;;
      --strict)    STRICT=1 ;;
      --open-only) OPEN_ONLY=1 ;;
      *) echo "ERROR: unknown option '$arg'" >&2; exit 1 ;;
    esac
  done
fi
[ "$STRICT" = "1" ] && INFER=0

# ---- ownership -------------------------------------------------------------
# claimed_lane <id> -> prints owning lane, or nothing if unclaimed.
claimed_lane() {
  local want=$1
  [ -f "$OWNERS" ] || return 0
  awk -F'\t' -v id="$want" '$2==id { lane=$3 } END { if (lane != "") print lane }' "$OWNERS"
}

# classify <id> -> MINE-CLAIMED | MINE-INFERRED | OTHER:<lane> | UNATTRIBUTED
classify() {
  local id=$1 owner
  owner=$(claimed_lane "$id")
  if [ -n "$owner" ]; then
    [ "$owner" = "$LANE" ] && { echo "MINE-CLAIMED"; return; }
    echo "OTHER:$owner"; return
  fi
  if [ "$INFER" = "1" ] && { [ "$id" = "val-$LANE" ] || [[ "$id" == "val-$LANE-"* ]]; }; then
    echo "MINE-INFERRED"; return
  fi
  echo "UNATTRIBUTED"
}

# ---- verification ----------------------------------------------------------
# is_dtu_type <type> -> 0 if this row names something the DTU CLI can probe.
is_dtu_type() {
  case "${1:-}" in dtu|container|dt|twin) return 0 ;; *) return 1 ;; esac
}

# probe_state <id> -> PRESENT | ABSENT | UNKNOWN
# UNKNOWN is deliberately distinct from ABSENT: a CLI that is missing, broken,
# or reporting an unrecognised error must never be read as "the container died".
probe_state() {
  local id=$1 out rc lc
  out=$("$DT_CLI" status "$id" 2>&1); rc=$?
  if [ "$rc" -eq 0 ]; then echo PRESENT; return; fi
  lc=$(printf '%s' "$out" | tr '[:upper:]' '[:lower:]')
  case "$lc" in
    *"not found"*|*"does not exist"*|*"no such environment"*) echo ABSENT; return ;;
  esac
  echo UNKNOWN
}

# canonical_cmd <id> -> the ONLY destroy form the CLI actually accepts.
canonical_cmd() { printf '%s destroy %s' "$DT_CLI" "$1"; }

# cmd_form <type> <id> <recorded> -> CANONICAL | REPAIRED:<why> | VERBATIM
# The recorded text is advisory. For DTU rows we rebuild from the id column, so
# an unsupported flag is ignored rather than executed-and-failed.
cmd_form() {
  local type=$1 id=$2 rec=${3:-}
  if ! is_dtu_type "$type"; then echo "VERBATIM"; return; fi
  [ "$rec" = "$(canonical_cmd "$id")" ] && { echo "CANONICAL"; return; }
  local why=""
  case "$rec" in
    *--force*)   why="unsupported-flag(--force)" ;;
    *"|| true"*) why="rc-masked(|| true)" ;;
    "")          why="empty" ;;
    *)           why="non-canonical" ;;
  esac
  echo "REPAIRED:$why"
}

# ---- commands --------------------------------------------------------------
case "$CMD" in

  claim)
    [ "$#" -ge 1 ] || { echo "ERROR: claim requires at least one ID" >&2; exit 1; }
    mkdir -p "$BATCH_DIR"
    rc=0
    for id in "$@"; do
      case "$id" in --*) continue ;; esac
      if [[ "$LANE$id" == *$'\t'* || "$LANE$id" == *$'\n'* ]]; then
        echo "ERROR: LANE/ID must not contain tabs or newlines" >&2; rc=1; continue
      fi
      owner=$(claimed_lane "$id")
      if [ -n "$owner" ] && [ "$owner" != "$LANE" ]; then
        echo "REFUSED: id=$id is already claimed by lane '$owner' — not stealing it" >&2
        rc=1; continue
      fi
      if [ "$owner" = "$LANE" ]; then
        echo "ALREADY-CLAIMED: id=$id lane=$LANE"; continue
      fi
      printf '%s\t%s\t%s\n' "$(date -u +%FT%TZ)" "$id" "$LANE" >> "$OWNERS"
      echo "CLAIMED: id=$id lane=$LANE"
    done
    exit "$rc"
    ;;

  list|teardown|reconcile|audit) ;;
  *) echo "ERROR: unknown command '$CMD' (want: list|claim|teardown|reconcile|audit)" >&2; exit 1 ;;
esac

if [ ! -f "$LEDGER" ]; then
  echo "LANE-TEARDOWN: no ledger ($LEDGER) — nothing to do"; exit 0
fi
[ -r "$LEDGER" ] || { echo "ERROR: ledger not readable: $LEDGER" >&2; exit 2; }

# ---- audit: read-only, batch-wide, destroys and mutates NOTHING ------------
if [ "$CMD" = "audit" ]; then
  echo "LEDGER-AUDIT  batch=$BATCH_DIR  ledger=$LEDGER  (READ-ONLY: nothing is destroyed or written)"
  echo "  probe=$DT_CLI status <id>   scope=$( [ "$OPEN_ONLY" = 1 ] && echo 'open rows only' || echo 'every row' )"
  echo
  printf '  %-4s %-15s %-10s %-7s %-14s %-9s %-22s %s\n' \
    LINE ID TYPE STATUS OWNER FORM VERDICT RECORDED-CMD
  n_stale=0; n_live=0; n_leaked=0; n_unrunnable=0; n_unknown=0; n_rows=0
  ln=0
  while IFS=$'\t' read -r ts type id status destroy || [ -n "${ts:-}" ]; do
    ln=$(( ln + 1 ))
    [ -z "${ts:-}" ] && continue
    [ -z "${id:-}" ] && continue
    [ "$OPEN_ONLY" = 1 ] && [ "${status:-}" != "open" ] && continue
    n_rows=$(( n_rows + 1 ))
    owner=$(claimed_lane "$id"); owner=${owner:-<unclaimed>}
    form=$(cmd_form "${type:-}" "$id" "${destroy:-}")
    case "$form" in *unsupported-flag*) n_unrunnable=$(( n_unrunnable + 1 )) ;; esac
    if is_dtu_type "${type:-}"; then state=$(probe_state "$id"); else state=UNPROBEABLE; fi
    verdict="OK"
    case "${status:-}/$state" in
      open/ABSENT)      verdict="STALE-OPEN(reconcilable)"; n_stale=$(( n_stale + 1 )) ;;
      open/PRESENT)     verdict="LIVE-OPEN(do-not-touch)";  n_live=$(( n_live + 1 )) ;;
      open/UNKNOWN)     verdict="UNKNOWN(fails-closed)";    n_unknown=$(( n_unknown + 1 )) ;;
      open/UNPROBEABLE) verdict="OPEN(unverifiable-type)" ;;
      */PRESENT)        verdict="LEAKED(swept-but-alive)";  n_leaked=$(( n_leaked + 1 )) ;;
      */UNKNOWN)        verdict="closed/UNKNOWN" ;;
    esac
    printf '  %-4s %-15s %-10s %-7s %-14s %-9s %-22s %s\n' \
      "$ln" "$id" "${type:-}" "${status:-}" "$owner" "$state" "$verdict" "${destroy:-}"
  done < "$LEDGER"
  echo
  echo "AUDIT SUMMARY  rows=$n_rows  stale-open=$n_stale  live-open=$n_live  leaked=$n_leaked  unrunnable-cmd=$n_unrunnable  unknown=$n_unknown"
  [ "$n_leaked" -gt 0 ] && echo "  !! LEAKED rows are marked swept while the container is ALIVE — the ledger is over-reporting reclaim."
  [ "$n_stale"  -gt 0 ] && echo "  -> STALE-OPEN rows are reclaimable with: $0 \"$BATCH_DIR\" <lane> reconcile --yes"
  exit 0
fi

# ---- list / teardown / reconcile share one classification pass -------------
sel_ids=(); sel_cmds=(); sel_why=(); sel_types=()
prot_ids=(); prot_why=()
malformed=0
while IFS=$'\t' read -r ts type id status destroy || [ -n "${ts:-}" ]; do
  [ -z "${ts:-}" ] && continue
  if [ -z "${status:-}" ] || [ -z "${id:-}" ]; then
    malformed=$(( malformed + 1 )); continue
  fi
  [ "$status" = "open" ] || continue
  k=$(classify "$id")
  case "$k" in
    MINE-CLAIMED|MINE-INFERRED)
      sel_ids+=("$id"); sel_cmds+=("${destroy:-}"); sel_why+=("$k"); sel_types+=("${type:-}") ;;
    *)
      prot_ids+=("$id"); prot_why+=("$k") ;;
  esac
done < "$LEDGER"

echo "LANE-TEARDOWN  batch=$BATCH_DIR  lane=$LANE  cmd=$CMD  ledger=$LEDGER"
echo "  ownership: claims=$( [ -f "$OWNERS" ] && wc -l < "$OWNERS" || echo 0 ) rows  inference=$( [ "$INFER" = 1 ] && echo on || echo off )$( [ "$STRICT" = 1 ] && echo ' (--strict)' )"
echo
echo "SELECTED (this lane's rows) — ${#sel_ids[@]}"
for i in "${!sel_ids[@]}"; do
  f=$(cmd_form "${sel_types[$i]}" "${sel_ids[$i]}" "${sel_cmds[$i]}")
  printf '  %-14s [%s] form=%-28s %s\n' "${sel_ids[$i]}" "${sel_why[$i]}" "$f" "${sel_cmds[$i]}"
done
[ "${#sel_ids[@]}" -eq 0 ] && echo "  (none)"
echo
echo "PROTECTED (never touched by lane '$LANE') — ${#prot_ids[@]}"
for i in "${!prot_ids[@]}"; do
  printf '  %-14s [%s]\n' "${prot_ids[$i]}" "${prot_why[$i]}"
done
[ "${#prot_ids[@]}" -eq 0 ] && echo "  (none)"
[ "$malformed" -gt 0 ] && echo && echo "NOTE: $malformed malformed ledger line(s) ignored and preserved verbatim"

if [ "$CMD" = "list" ]; then exit 0; fi

if [ "${#sel_ids[@]}" -eq 0 ]; then
  # ---- NEAR-MISS GUARD (model_performance-ye80) ----------------------------
  # A lane name that ALMOST matches must never exit 0 having done nothing.
  #
  # Observed 2026-09-05, mid-recovery, with six containers still billing:
  #     lane_teardown.sh <batch> drbf teardown --yes
  #       -> "TEARDOWN: lane 'drbf' owns no open rows - nothing to do"  (exit 0)
  # while SIX rows were open under the full name `drbf-compaction-notice-ab`.
  #
  # WHY it looked clean: the ids (val-drbf-a...) are exactly what inference
  # WOULD have selected for lane `drbf`, but classify() consults claims FIRST
  # and those rows carry a claim under the LONG name, so classify() returned
  # OTHER:drbf-compaction-notice-ab and every candidate landed in PROTECTED.
  # Empty selection then printed a success message. This is the one path an
  # operator reaches for in an emergency: on it, a near miss must be an ERROR
  # that names the candidates. (Same class as 2nz's: a guard that reports
  # success while doing nothing.)
  #
  # Deliberately NOT "any protected row is an error": a lane that provisioned
  # nothing runs teardown as a matter of course, and failing it merely because
  # some unrelated lane holds rows would make every clean lane look broken.
  # Only a NEAR MISS -- prefix-related on a token boundary, or an id inference
  # would have claimed -- is treated as an operator typo.
  near_owner=(); near_count=()
  for i in "${!prot_ids[@]}"; do
    pid=${prot_ids[$i]}; pwhy=${prot_why[$i]}
    powner=""; case "$pwhy" in OTHER:*) powner=${pwhy#OTHER:} ;; esac
    hit=0
    # (a) an id inference WOULD have selected for this lane, had no claim won.
    if [ "$pid" = "val-$LANE" ] || [[ "$pid" == "val-$LANE-"* ]]; then hit=1; fi
    # (b) owner name and LANE are prefix-related on a token boundary -- covers
    #     BOTH directions, because this batch's ledger genuinely carries both
    #     short work-item ids and long manifest names as owners.
    if [ -n "$powner" ] && { [[ "$powner" == "$LANE-"* ]] || [[ "$LANE" == "$powner-"* ]]; }; then hit=1; fi
    [ "$hit" = 1 ] || continue
    label=${powner:-<unattributed>}
    found=0
    for j in "${!near_owner[@]}"; do
      if [ "${near_owner[$j]}" = "$label" ]; then
        near_count[$j]=$(( near_count[j] + 1 )); found=1; break
      fi
    done
    [ "$found" = 0 ] && { near_owner+=("$label"); near_count+=(1); }
  done

  if [ "${#near_owner[@]}" -gt 0 ]; then
    {
      echo
      echo "ERROR: lane '$LANE' owns no open rows, but open rows ARE held under a NEAR-MISS lane name."
      echo "       Refusing to report success for a teardown that would do nothing."
      echo
      for j in "${!near_owner[@]}"; do
        printf "   candidate: %-32s %s open row(s)\n" "${near_owner[$j]}" "${near_count[$j]}"
      done
      echo
      echo "Re-run with the EXACT owner name, e.g.:"
      echo "   $0 \"$BATCH_DIR\" ${near_owner[0]} $CMD --yes"
      echo "Or inspect first (read-only, destroys nothing):"
      echo "   $0 \"$BATCH_DIR\" $LANE audit"
    } >&2
    exit 4
  fi

  echo
  echo "${CMD^^}: lane '$LANE' owns no open rows - nothing to do"
  if [ "${#prot_ids[@]}" -gt 0 ]; then
    echo "  (${#prot_ids[@]} open row(s) belong to other lanes; none resemble '$LANE')"
  fi
  exit 0
  # ---- end near-miss guard -------------------------------------------------
fi

# ---- decide, per row, whether it may leave `open` --------------------------
# reconcile: probe only, never destroy.  teardown: destroy, then probe.
echo
declare -A done_ids=()
failed=0; skipped_live=0; skipped_unknown=0; already_absent=0; unverifiable=0

for i in "${!sel_ids[@]}"; do
  id=${sel_ids[$i]}; d=${sel_cmds[$i]}; type=${sel_types[$i]}

  if ! is_dtu_type "$type"; then
    if [ "$CMD" = "reconcile" ]; then
      echo ">> id=$id type=$type: NOT PROBEABLE — reconcile skips it (use teardown)"
      unverifiable=$(( unverifiable + 1 )); continue
    fi
    # Legacy path, weaker guarantee, stated out loud rather than hidden.
    echo ">> destroying id=$id type=$type VERIFY=unverifiable: $d"
    if [ -z "$d" ]; then
      echo "   FAILED: empty destroy_cmd for id=$id" >&2; failed=$(( failed + 1 )); continue
    fi
    if [ "$ASSUME_YES" != "1" ]; then echo "   (dry-run)"; continue; fi
    if bash -c "$d"; then
      echo "   ok (rc=0; container existence NOT independently verified)"
      done_ids["$id"]=1; unverifiable=$(( unverifiable + 1 ))
    else
      rc=$?; echo "   FAILED (rc=$rc) id=$id: $d" >&2; failed=$(( failed + 1 ))
    fi
    continue
  fi

  before=$(probe_state "$id")

  if [ "$before" = "PRESENT" ] && [ "$CMD" = "reconcile" ]; then
    echo ">> id=$id: container is PRESENT — reconcile REFUSES to reclaim a live row"
    skipped_live=$(( skipped_live + 1 )); continue
  fi
  if [ "$before" = "UNKNOWN" ]; then
    echo ">> id=$id: probe UNKNOWN — failing CLOSED, row stays open" >&2
    skipped_unknown=$(( skipped_unknown + 1 )); continue
  fi
  if [ "$before" = "ABSENT" ]; then
    echo ">> id=$id: already ABSENT — no destroy needed, row is STALE-OPEN"
    if [ "$ASSUME_YES" != "1" ]; then echo "   (dry-run: would mark swept)"; else
      done_ids["$id"]=1; already_absent=$(( already_absent + 1 ))
    fi
    continue
  fi

  # PRESENT + teardown: rebuild the command from the id column, so a recorded
  # `--force` (or `|| true`, or any other noise) is IGNORED, not executed.
  canon=$(canonical_cmd "$id")
  form=$(cmd_form "$type" "$id" "$d")
  case "$form" in
    REPAIRED:*) echo ">> destroying id=$id  [recorded cmd ${form#REPAIRED:} — IGNORED]" ;;
    *)          echo ">> destroying id=$id" ;;
  esac
  echo "   recorded : ${d:-<empty>}"
  echo "   running  : $canon"
  if [ "$ASSUME_YES" != "1" ]; then echo "   (dry-run)"; continue; fi
  bash -c "$canon"; rc=$?
  after=$(probe_state "$id")
  echo "   rc=$rc  probe-after=$after"
  if [ "$after" = "ABSENT" ]; then
    echo "   VERIFIED GONE — row may be marked swept"
    done_ids["$id"]=1
  else
    echo "   NOT VERIFIED GONE (probe=$after) — row STAYS OPEN" >&2
    failed=$(( failed + 1 ))
  fi
done

if [ "$ASSUME_YES" != "1" ]; then
  echo
  echo "DRY-RUN: nothing was destroyed, nothing was written. Re-run with --yes."
  exit 0
fi

# ---- flip ONLY our verified rows, under a lock, on a FRESH read ------------
# Held for milliseconds and taken after the (slow) destroys, so it never blocks
# a peer lane. NOTE: upstream infra_ledger.sh takes no lock, so this closes the
# lane_teardown-vs-lane_teardown race only — see README for the upstream fix.
flipped=0
if [ "${#done_ids[@]}" -gt 0 ]; then
  mkdir -p "$BATCH_DIR"
  exec 200>"$LOCK"
  flock -w 30 200 || { echo "ERROR: could not lock $LOCK within 30s — ledger NOT updated" >&2; exit 1; }
  tmp=$(mktemp "$BATCH_DIR/.infra.XXXXXX") || { echo "ERROR: mktemp failed" >&2; exit 1; }
  while IFS=$'\t' read -r ts type id status destroy || [ -n "${ts:-}" ]; do
    if [ -z "${ts:-}" ]; then printf '\n' >> "$tmp"; continue; fi
    if [ "${status:-}" = "open" ] && [ -n "${done_ids[${id:-}]:-}" ]; then
      status=swept; flipped=$(( flipped + 1 ))
    fi
    printf '%s\t%s\t%s\t%s\t%s\n' "$ts" "${type:-}" "${id:-}" "${status:-}" "${destroy:-}" >> "$tmp"
  done < "$LEDGER"
  chmod --reference="$LEDGER" "$tmp" 2>/dev/null || true
  mv "$tmp" "$LEDGER"
  flock -u 200
  exec 200>&-
fi

echo
echo "${CMD^^}: lane=$LANE verified-gone=${#done_ids[@]} rows-flipped=$flipped"
echo "  already-absent=$already_absent failed=$failed live-skipped=$skipped_live unknown-skipped=$skipped_unknown unverifiable=$unverifiable protected-untouched=${#prot_ids[@]}"
[ "$skipped_live" -gt 0 ] && echo "  NOTE: $skipped_live live row(s) left open on purpose — a live container is never reclaimed."
[ "$failed" -gt 0 ] && { echo "${CMD^^}: $failed row(s) still open — fix and re-run"; exit 1; }
exit 0
