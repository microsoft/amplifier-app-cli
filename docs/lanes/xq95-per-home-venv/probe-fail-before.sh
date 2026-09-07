#!/usr/bin/env bash
# The same two-homes probe with per-home environments OFF -- i.e. the shipped
# behaviour -- run against a THROWAWAY base environment.
#
# The throwaway is not politeness. With the feature off, installs target the
# base environment, so pointing this at a real install is precisely the
# corruption q99f had to repair from a backup.
#
# Expect: FAILURES. That is the point -- it is the fail-before half of
# probe-overlay-isolation.sh's pass-after.

set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
D="$(mktemp -d "${TMPDIR:-/tmp}/xq95-failbefore.XXXXXX")"

uv venv --python "$(command -v python3)" "$D/base" >/dev/null 2>&1 \
  || { echo "could not create a throwaway base environment"; exit 2; }

echo "throwaway base: $D/base"
AMPLIFIER_HOME_ENV=0 PROBE_PYTHON="$D/base/bin/python" \
  bash "$HERE/probe-overlay-isolation.sh"
rc=$?
echo
echo "the shared .pth the two homes fought over, and who won:"
for f in "$D"/base/lib/python*/site-packages/_editable_impl_amplifier_module_probe.pth; do
  [ -f "$f" ] && printf '  %s -> %s\n' "$(basename "$f")" "$(head -1 "$f")"
done
echo
echo "exit $rc  (non-zero is the expected fail-before result)"
exit 0
