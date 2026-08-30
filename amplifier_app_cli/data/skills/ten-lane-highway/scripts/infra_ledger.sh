#!/usr/bin/env bash
# infra_ledger.sh — a durable ledger of provisioned infrastructure, plus a sweep
# that tears it down. So a highway run can never leak a worktree, container, or
# temp dir it created: every provisioned thing is recorded here with the exact
# command that destroys it, and one `sweep` reclaims them all.
#
# Usage:
#   infra_ledger.sh BATCH_DIR add  TYPE ID DESTROY_CMD...
#   infra_ledger.sh BATCH_DIR list
#   infra_ledger.sh BATCH_DIR sweep
#
# Rows live in BATCH_DIR/infra.tsv, one per line, tab-separated:
#   ts <TAB> type <TAB> id <TAB> status <TAB> destroy_cmd
# THIS script is the ONLY writer of infra.tsv — never hand-edit that file.
#
#   add    Append one row with status=open. DESTROY_CMD... is the (possibly
#          multi-word) command that reclaims the resource; it is stored verbatim
#          and later run via `bash -c`.
#   list   Print the open rows; always exit 0.
#   sweep  Run each OPEN row's destroy_cmd. On rc=0 mark it swept; otherwise
#          leave it open and print the failure. Exit nonzero if any row is still
#          open afterwards. Idempotent: already-swept rows are never re-run, so
#          re-sweeping a fully-swept ledger runs nothing and exits 0.
#
# NOT -e: a failing destroy_cmd during sweep is an expected, handled outcome —
# it must not abort the whole sweep.
set -uo pipefail

BATCH_DIR=${1:?BATCH_DIR required}
CMD=${2:?command required (add|list|sweep)}
LEDGER="$BATCH_DIR/infra.tsv"

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
    tmp=$(mktemp "$BATCH_DIR/.infra.XXXXXX")
    remaining=0
    while IFS=$'\t' read -r ts type id status destroy; do
      [ -z "${ts:-}" ] && continue   # skip blank lines
      if [ "$status" = "open" ]; then
        echo ">> sweeping type=$type id=$id: $destroy"
        if bash -c "$destroy"; then
          status=swept
          echo "   swept ok"
        else
          rc=$?
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
