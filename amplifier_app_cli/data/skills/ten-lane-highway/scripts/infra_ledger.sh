#!/usr/bin/env bash
# infra_ledger.sh — a durable ledger of provisioned infrastructure, plus a sweep
# that tears it down. So a highway run can never leak a worktree, container, or
# temp dir it created: every provisioned thing is recorded here with the exact
# command that destroys it, and one `sweep` reclaims them all.
#
# Usage:
#   infra_ledger.sh BATCH_DIR add  TYPE ID DESTROY_CMD...
#   infra_ledger.sh BATCH_DIR list
#   infra_ledger.sh BATCH_DIR sweep [--all-owners]
#
# Rows live in BATCH_DIR/infra.tsv, one per line, tab-separated:
#   ts <TAB> type <TAB> id <TAB> status <TAB> destroy_cmd
# THIS script is the ONLY writer of infra.tsv — never hand-edit that file.
# Rows are attributed to a lane by a sibling BATCH_DIR/infra.owners.tsv
#   ts <TAB> id <TAB> lane
# written by the batch's lane-scoped teardown tool, not by this script.
#
#   add    Append one row with status=open. DESTROY_CMD... is the (possibly
#          multi-word) command that reclaims the resource; it is stored verbatim
#          and later run via `bash -c`.
#   list   Print the open rows; always exit 0.
#   sweep  THE MANAGER'S BATCH-CLOSE VERB, NEVER A LANE'S. It runs EVERY open
#          row's destroy_cmd, so a lane calling it destroys other lanes' live
#          infrastructure. A lane tearing down its OWN rows uses the batch's
#          lane-scoped teardown tool (lane_teardown.sh) instead.
#          Refuses with exit 3, having run NOTHING, when the open rows span
#          more than one owner or any row is unattributable; the manager
#          closing the batch passes --all-owners to proceed anyway.
#          Otherwise, per open row: rc=0 marks it `swept`; a rc!=0 whose output
#          matches the NARROW already-gone signature marks it
#          `swept:already-absent` (recorded distinctly — the row is closed but
#          this sweep did not perform the teardown); any other failure leaves
#          the row open and is printed. Exit nonzero if any row is still open
#          afterwards. Idempotent: already-swept rows are never re-run, so
#          re-sweeping a fully-swept ledger runs nothing and exits 0.
#
# NOT -e: a failing destroy_cmd during sweep is an expected, handled outcome —
# it must not abort the whole sweep.
set -uo pipefail

BATCH_DIR=${1:?BATCH_DIR required}
CMD=${2:?command required (add|list|sweep)}
LEDGER="$BATCH_DIR/infra.tsv"

# Signatures meaning "the thing you asked me to destroy does not exist".
# Deliberately NARROW: a blanket exit-code amnesty would destroy the signal
# that a REAL teardown failed, which is the whole reason sweep checks rc
# (Rule 14: nothing the highway stands up should outlive it).
ALREADY_GONE_RE=${ALREADY_GONE_RE:-"environment not found|not found|no such|does not exist|doesn't exist|unknown (environment|container|project)"}

case "$CMD" in
  add)
    TYPE=${3:?TYPE required}
    ID=${4:?ID required}
    shift 4
    [ "$#" -ge 1 ] || { echo "ERROR: add requires DESTROY_CMD..." >&2; exit 1; }
    DESTROY="$*"
    # A row is one TSV line: tabs/newlines in a field would corrupt the ledger.
    if [[ "$TYPE$ID$DESTROY" == *$'\t'* || "$TYPE$ID$DESTROY" == *$'\n'* ]]; then
      echo "ERROR: TYPE/ID/DESTROY_CMD must not contain tabs or newlines" >&2; exit 1
    fi
    mkdir -p "$BATCH_DIR"
    printf '%s\t%s\t%s\t%s\t%s\n' "$(date -u +%FT%TZ)" "$TYPE" "$ID" "open" "$DESTROY" >> "$LEDGER"
    echo "ADDED: type=$TYPE id=$ID status=open destroy='$DESTROY'"
    ;;

  list)
    [ -f "$LEDGER" ] || exit 0
    awk -F'\t' '$4=="open"' "$LEDGER" || true
    exit 0
    ;;

  sweep)
    [ -f "$LEDGER" ] || { echo "SWEEP: no ledger ($LEDGER) - nothing to do"; exit 0; }

    # ---- MULTI-LANE GUARD (model_performance-0rg) -------------------------
    # sweep is the MANAGER's BATCH-CLOSE verb. Its only predicate used to be
    # `status == open`, so the FIRST caller destroyed EVERY lane's live
    # infrastructure. Observed 2026-09-02: one foreign sweep took lane l1's
    # three DTUs and lane 161's three, 35 minutes into their measurements.
    # A lane tearing down its OWN rows uses the lane-scoped tool:
    #   .amplifier/evaluation/tools/lane_teardown.sh BATCH_DIR LANE teardown
    ALL_OWNERS=0
    for a in "$@"; do [ "$a" = "--all-owners" ] && ALL_OWNERS=1; done
    if [ "$ALL_OWNERS" != 1 ]; then
      OWNERS_FILE="$BATCH_DIR/infra.owners.tsv"
      owners=$(awk -F'\t' -v of="$OWNERS_FILE" '
        BEGIN { while ((getline line < of) > 0) { split(line, f, "\t"); own[f[2]] = f[3] } }
        $4 ~ /^open/ { print ($3 in own) ? own[$3] : "<unattributed:" $3 ">" }
      ' "$LEDGER" | sort -u)
      n=$(printf '%s\n' "$owners" | grep -c . || true)
      if [ "${n:-0}" -gt 1 ] || printf '%s' "$owners" | grep -q '^<unattributed:'; then
        echo "REFUSING to sweep: open rows span more than one owner, or are unattributable." >&2
        printf '%s\n' "$owners" | sed 's/^/   owner: /' >&2
        echo "" >&2
        echo "sweep is the MANAGER's batch-close verb and destroys EVERY open row." >&2
        echo "A lane tearing down its OWN rows must use the lane-scoped tool:" >&2
        echo "  .amplifier/evaluation/tools/lane_teardown.sh $BATCH_DIR <lane> teardown --yes" >&2
        echo "The manager closing the batch passes --all-owners." >&2
        exit 3
      fi
    fi
    # ---- end guard --------------------------------------------------------

    tmp=$(mktemp "$BATCH_DIR/.infra.XXXXXX")
    remaining=0
    while IFS=$'\t' read -r ts type id status destroy; do
      [ -z "${ts:-}" ] && continue   # skip blank lines
      if [ "$status" = "open" ]; then
        echo ">> sweeping type=$type id=$id: $destroy"
        # Capture output so an "already gone" refusal can be recognised.
        out=$(bash -c "$destroy" 2>&1); rc=$?
        [ -n "$out" ] && printf '%s\n' "$out"
        if [ "$rc" -eq 0 ]; then
          status=swept
          echo "   swept ok"
        elif printf '%s' "$out" | grep -qiE "$ALREADY_GONE_RE"; then
          # model_performance-bqu: ALREADY GONE is the DESIRED end state of a
          # destroy, not a failure. Before this, infrastructure torn down by any
          # other path (a lane by hand, a manager recovery, a crash cleanup)
          # left a row that could NEVER be closed: the destroy_cmd failed
          # forever, so `sweep` never exited clean and SKILL.md's "do not treat
          # the highway as closed until it exits clean" became unsatisfiable.
          # Recorded DISTINCTLY from a real teardown so the two are never
          # confused in the ledger.
          status=swept:already-absent
          echo "   already absent - closing row (not a teardown this sweep performed)"
        else
          echo "   FAILED (rc=$rc) type=$type id=$id: $destroy" >&2
          remaining=$(( remaining + 1 ))
        fi
      fi
      printf '%s\t%s\t%s\t%s\t%s\n' "$ts" "$type" "$id" "$status" "$destroy" >> "$tmp"
    done < "$LEDGER"
    mv "$tmp" "$LEDGER"
    if [ "$remaining" -gt 0 ]; then
      echo "SWEEP: $remaining row(s) still open (destroy_cmd failed) - re-run after fixing"
      exit 1
    fi
    echo "SWEEP: all rows swept"
    exit 0
    ;;

  *)
    echo "ERROR: unknown command '$CMD' (want: add|list|sweep)" >&2
    exit 1
    ;;
esac
