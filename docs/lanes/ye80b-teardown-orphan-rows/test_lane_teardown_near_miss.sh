#!/usr/bin/env bash
# Proof harness for model_performance-ye80 SUB-FINDING 1 — lane_teardown.sh's
# near-miss footgun.
#
# WHY THIS IS A STANDALONE SCRIPT AND NOT A pytest FILE
#   lane_teardown.sh lives in ANOTHER repo (openai-evals-team-ci) and is
#   untracked even there. This lane owns only amplifier-app-cli, so the fix
#   cannot land here and a pytest file here must not depend on a path outside
#   this repo. This harness therefore takes the script under test as an
#   argument, runs it against a throwaway batch dir in $TMPDIR, and proves
#   fail-before / pass-after without editing anything it does not own.
#
# USAGE
#   test_lane_teardown_near_miss.sh /path/to/lane_teardown.sh
#
#   Exit 0  = the script under test HAS the near-miss guard and still protects
#             other lanes' rows (pass-after).
#   Exit 1  = one or more expectations failed (this is what the UNPATCHED
#             script does — that failure IS the fail-before evidence).
#
# The DTU CLI is stubbed through the script's own AMPLIFIER_DT_CLI seam, so
# nothing real is ever probed or destroyed. Every destroy is observable: the
# stub deletes a state file, so "ran nothing" is proven by the file still being
# there rather than inferred from an exit code (the tests/test_ten_lane_highway_
# infra_ledger.py standard — the buggy path prints a success message, so an
# exit code alone cannot tell "refused" from "ran and failed").
set -uo pipefail

SUT=${1:?usage: $0 /path/to/lane_teardown.sh}
[ -r "$SUT" ] || { echo "ERROR: cannot read $SUT" >&2; exit 2; }

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
fails=0

pass() { printf '  PASS  %s\n' "$1"; }
fail() { printf '  FAIL  %s\n' "$1"; fails=$(( fails + 1 )); }

# --- stub DTU CLI: a container exists iff $DT_STATE/<id> exists -------------
mkdir -p "$WORK/bin" "$WORK/state"
cat > "$WORK/bin/dt-stub" <<'STUB'
#!/usr/bin/env bash
verb=${1:-}; id=${2:-}
case "$verb" in
  status)  [ -f "$DT_STATE/$id" ] && { echo "running"; exit 0; }
           echo "Environment '$id' not found" >&2; exit 1 ;;
  destroy) rm -f "$DT_STATE/$id"; echo "destroyed $id"; exit 0 ;;
  *) echo "unknown verb $verb" >&2; exit 2 ;;
esac
STUB
chmod +x "$WORK/bin/dt-stub"
export AMPLIFIER_DT_CLI="$WORK/bin/dt-stub"
export DT_STATE="$WORK/state"

# --- the ledger, reproducing the 2026-09-05 state ---------------------------
# Six rows owned by the DEAD lane under its LONG name, four owned by two LIVE
# lanes. Ids use the short form `val-drbf-*`, exactly as recorded that day.
build_batch() {
  local b="$1"; rm -rf "$b"; mkdir -p "$b"
  : > "$b/infra.tsv"; : > "$b/infra.owners.tsv"
  add_row() { # id owner
    printf '2026-09-06T02:18:49Z\tdtu\t%s\topen\tamplifier-digital-twin destroy %s\n' "$1" "$1" >> "$b/infra.tsv"
    printf '2026-09-06T02:18:49Z\t%s\t%s\n' "$1" "$2" >> "$b/infra.owners.tsv"
    : > "$DT_STATE/$1"
  }
  for s in a b c a2 b2 c2; do add_row "val-drbf-$s" "drbf-compaction-notice-ab"; done
  for s in a b; do add_row "val-vbs-$s" "vbs-cache-residency"; done
  for s in a b; do add_row "val-otr-$s" "otr-armb-medium-root"; done
}

open_rows() { awk -F'\t' '$4=="open"' "$1/infra.tsv" | wc -l | tr -d ' '; }
alive()     { ls "$DT_STATE" | wc -l | tr -d ' '; }

echo "== SUT: $SUT"
echo

# ---------------------------------------------------------------------------
echo "CASE 1 — the incident: a NEAR-MISS lane name must FAIL LOUDLY"
echo "         (2026-09-05: \`lane_teardown.sh <batch> drbf teardown --yes\`"
echo "          exited 0 saying 'owns no open rows' while six were open)"
B="$WORK/b1"; build_batch "$B"
out=$(bash "$SUT" "$B" drbf teardown --yes 2>&1); rc=$?
if [ "$rc" -eq 0 ]; then
  fail "exited 0 on a near-miss name (the footgun: reports success, does nothing)"
else
  pass "exited non-zero ($rc) instead of reporting success"
fi
# Deliberately requires the candidate on ONE line with its marker. The
# UNPATCHED script also prints "drbf-compaction-notice-ab" -- in its PROTECTED
# listing, right before exiting 0 -- so a bare name match would pass against
# the very bug under test.
if printf '%s\n' "$out" | grep -q "candidate:.*drbf-compaction-notice-ab"; then
  pass "named the candidate lane that DOES own the open rows"
else
  fail "did not name the candidate lane — an operator cannot act on this"
fi
if [ "$(open_rows "$B")" = "10" ] && [ "$(alive)" = "10" ]; then
  pass "destroyed nothing and flipped no row while refusing"
else
  fail "refusal was not inert (open=$(open_rows "$B") alive=$(alive), want 10/10)"
fi
echo

# ---------------------------------------------------------------------------
echo "CASE 2 — the EXACT lane name still works, unchanged"
B="$WORK/b2"; build_batch "$B"
out=$(bash "$SUT" "$B" drbf-compaction-notice-ab teardown --yes 2>&1); rc=$?
if [ "$rc" -eq 0 ]; then pass "exact name exits 0"; else fail "exact name exits $rc: $out"; fi
if printf '%s' "$out" | grep -q "verified-gone=6"; then
  pass "verified-gone=6 (the transcript recorded that day)"
else
  fail "did not verify-tear-down its six rows"
fi
if printf '%s' "$out" | grep -q "rows-flipped=6"; then
  pass "rows-flipped=6"
else
  fail "did not flip its six rows"
fi
echo

# ---------------------------------------------------------------------------
echo "CASE 3 — a LIVE lane's rows are NEVER touched by either path"
echo "         (dead lane and two live lanes hold rows simultaneously)"
if printf '%s' "$out" | grep -q "protected-untouched=4"; then
  pass "protected-untouched=4 (the two live lanes' rows)"
else
  fail "protected-untouched is not 4 — the protection regressed"
fi
if [ "$(open_rows "$B")" = "4" ]; then
  pass "exactly the 4 live-lane rows remain open"
else
  fail "wrong number of rows left open: $(open_rows "$B")"
fi
live_gone=0
for s in val-vbs-a val-vbs-b val-otr-a val-otr-b; do
  [ -f "$DT_STATE/$s" ] || { fail "LIVE lane's container $s was DESTROYED"; live_gone=1; }
done
[ "$live_gone" = 0 ] && pass "every live-lane container still exists (observable, not inferred)"
echo

# ---------------------------------------------------------------------------
echo "CASE 4 — a genuinely idle lane still exits 0 (no new false failure)"
echo "         a lane that provisioned nothing must not fail merely because"
echo "         unrelated lanes hold rows"
B="$WORK/b4"; build_batch "$B"
out=$(bash "$SUT" "$B" zzz teardown --yes 2>&1); rc=$?
if [ "$rc" -eq 0 ]; then
  pass "unrelated idle lane exits 0"
else
  fail "unrelated idle lane now exits $rc — the guard is too broad"
fi
if [ "$(open_rows "$B")" = "10" ]; then
  pass "and touched nothing"
else
  fail "an idle lane's teardown changed the ledger"
fi
echo

# ---------------------------------------------------------------------------
echo "CASE 5 — the MIRROR near miss: long name typed, rows claimed short"
echo "         (this batch's ledger genuinely carries both conventions)"
B="$WORK/b5"; rm -rf "$B"; mkdir -p "$B"; : > "$B/infra.tsv"; : > "$B/infra.owners.tsv"
for s in a b; do
  printf '2026-09-06T02:18:49Z\tdtu\tval-vbs-%s\topen\tamplifier-digital-twin destroy val-vbs-%s\n' "$s" "$s" >> "$B/infra.tsv"
  printf '2026-09-06T02:18:49Z\tval-vbs-%s\tvbs\n' "$s" >> "$B/infra.owners.tsv"
  : > "$DT_STATE/val-vbs-$s"
done
out=$(bash "$SUT" "$B" vbs-cache-residency teardown --yes 2>&1); rc=$?
if [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -q "candidate"; then
  pass "long name against short-claimed rows also fails loudly, naming 'vbs'"
else
  fail "mirror near miss exited $rc without naming a candidate"
fi
echo

echo "=================================================="
if [ "$fails" -eq 0 ]; then
  echo "RESULT: PASS — near-miss guard present, protection intact"
  exit 0
fi
echo "RESULT: FAIL — $fails expectation(s) unmet"
exit 1
