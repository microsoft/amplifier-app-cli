#!/usr/bin/env bash
# lane_teardown.sh — THIN SHIM. The implementation lives in amplifier-app-cli.
#
# PROPOSED replacement for
#   openai-evals-team-ci/.amplifier/evaluation/tools/lane_teardown.sh
# filed by lane giwq (item model_performance-giwq) against amplifier-app-cli.
# This lane owns amplifier-app-cli only, so it cannot install this file; the
# evals repo must adopt it. Until it does, TWO copies of a destructive tool
# exist and can drift — which is the defect model_performance-giwq exists to end.
#
# WHY A SHIM RATHER THAN A DELETION
#   Every operator instruction written before 2026-09-06 — including this
#   batch's own GOAL.md files, already handed to running lanes — spells the
#   path `.amplifier/evaluation/tools/lane_teardown.sh`. Deleting the file
#   turns each of those into `No such file or directory` at exactly the moment
#   an operator is mid-incident. A shim keeps the old path working and makes
#   the shipped script the ONE implementation. Delete it once no live
#   instruction names it.
#
# WHY IT NEVER FALLS BACK TO A LOCAL COPY
#   A fallback implementation is a second implementation. If the shipped script
#   cannot be found this exits non-zero and says how to install it, because a
#   teardown tool that silently runs an older, unpatched code path is strictly
#   worse than one that refuses: the near-miss footgun (exit 0 while six rows
#   were open) is precisely what an out-of-date copy would reintroduce.
set -uo pipefail

REL="amplifier_app_cli/data/skills/ten-lane-highway/scripts/lane_teardown.sh"

find_shipped() {
  # 1. Explicit override always wins — the escape hatch for a dev checkout.
  if [ -n "${LANE_TEARDOWN_SH:-}" ]; then
    printf '%s' "$LANE_TEARDOWN_SH"; return 0
  fi
  # 2. Derive from the installed `amplifier` entry point:
  #    <tool>/bin/amplifier -> <tool>/lib/python3.*/site-packages/<REL>
  local amp root c
  amp=$(command -v amplifier 2>/dev/null) || amp=""
  if [ -n "$amp" ]; then
    root=$(cd "$(dirname "$(readlink -f "$amp")")/.." && pwd)
    for c in "$root"/lib/python3.*/site-packages/"$REL"; do
      [ -f "$c" ] && { printf '%s' "$c"; return 0; }
    done
  fi
  # 3. A sibling dev checkout, if one is present next to this repo.
  for c in "$HOME"/dev/amplifier-app-cli/"$REL"; do
    [ -f "$c" ] && { printf '%s' "$c"; return 0; }
  done
  return 1
}

SHIPPED=$(find_shipped) || {
  {
    echo "ERROR: lane_teardown.sh now ships WITH the ten-lane-highway skill, and this"
    echo "       machine has no copy of it. Refusing to run an unpinned teardown."
    echo
    echo "  expected at: <amplifier install>/$REL"
    echo
    echo "  fix (any one):"
    echo "    * install/update the CLI:  uv tool install --force git+https://github.com/microsoft/amplifier-app-cli@main"
    echo "    * or point at a checkout:  LANE_TEARDOWN_SH=/path/to/$REL $0 ..."
    echo
    echo "  read-only alternatives that need no teardown tool:"
    echo "    amplifier_app_cli/.../scripts/highway_status.sh BATCH_DIR WIDTH READY   # names orphan rows"
    echo "  do NOT substitute \`infra_ledger.sh ... sweep\`: it is the MANAGER's"
    echo "  batch-close verb and destroys EVERY lane's infrastructure."
  } >&2
  exit 127
}

exec bash "$SHIPPED" "$@"
