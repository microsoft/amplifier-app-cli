#!/usr/bin/env bash
# highway_readiness.sh — one batch-local observation of runnable Highway work.
#
# Usage: highway_readiness.sh publish BATCH_DIR RUNNABLE
#
# Runnable work is deliberately supplied by the manager: it has the batch's
# priority, conflict, and pre-composed-goal context that a generic queue probe
# cannot know. Consumers source this file so status and watchdog use precisely
# the same validation and freshness rules.
READINESS_FILE_NAME=.runnable-work

readiness_publish() {
  local batch_dir=${1:?BATCH_DIR required}
  local runnable=${2:?RUNNABLE required}
  local target tmp
  case "$runnable" in
    ''|*[!0-9]*) echo "ERROR: RUNNABLE must be a non-negative integer (got '$runnable')" >&2; return 2 ;;
  esac
  [ -d "$batch_dir" ] || { echo "ERROR: batch directory not found: $batch_dir" >&2; return 2; }

  target="$batch_dir/$READINESS_FILE_NAME"
  tmp=$(mktemp "$batch_dir/.runnable-work.XXXXXX")
  printf 'v1\t%s\n' "$runnable" > "$tmp"
  mv -f "$tmp" "$target"
}

readiness_load() {
  local batch_dir=${1:?BATCH_DIR required}
  local max_age=${2:-${HIGHWAY_READINESS_MAX:-1800}}
  local target line version runnable extra now mtime

  READINESS_STATE=unknown
  READINESS_RUNNABLE=
  READINESS_AGE_SECONDS=
  case "$max_age" in
    ''|*[!0-9]*) return 0 ;;
  esac

  target="$batch_dir/$READINESS_FILE_NAME"
  [ -f "$target" ] || return 0
  [ "$(wc -l < "$target")" -eq 1 ] || return 0
  IFS= read -r line < "$target" || return 0
  IFS=$'\t' read -r version runnable extra <<< "$line"
  [ "$version" = v1 ] && [ -n "$runnable" ] && [ -z "${extra:-}" ] || return 0
  case "$runnable" in
    *[!0-9]*) return 0 ;;
  esac

  now=$(date +%s)
  mtime=$(stat -c %Y "$target" 2>/dev/null) || return 0
  READINESS_AGE_SECONDS=$(( now - mtime ))
  [ "$READINESS_AGE_SECONDS" -lt 0 ] && READINESS_AGE_SECONDS=0
  if [ "$READINESS_AGE_SECONDS" -gt "$max_age" ]; then
    READINESS_STATE=stale
    return 0
  fi

  READINESS_RUNNABLE=$runnable
  if [ "$runnable" -gt 0 ]; then READINESS_STATE=runnable; else READINESS_STATE=drained; fi
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  set -euo pipefail
  case "${1:-}" in
    publish)
      [ "$#" -eq 3 ] || { echo "Usage: $0 publish BATCH_DIR RUNNABLE" >&2; exit 2; }
      readiness_publish "$2" "$3"
      ;;
    *)
      echo "Usage: $0 publish BATCH_DIR RUNNABLE" >&2
      exit 2
      ;;
  esac
fi