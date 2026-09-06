# Lane ye80b — the teardown footgun and orphaned ledger rows

**Item:** `model_performance-ye80` (project `model_performance`)
**Repo:** `microsoft/amplifier-app-cli`, branch `lane/ye80b-teardown-orphan-rows`
**Date:** 2026-09-06
**Outcome:** **A. RESOLVED** — the orphan-row deliverables are DONE and landed in
this repo. The near-miss deliverables are **DONE in substance** (fixed, proven
fail-before/pass-after) but **NOT-LANDABLE-HERE for a scope reason, not a cap
reason**: their only target lives in another repo. Patch + proof shipped as
artifacts, and the goal defect is reported below.

**Spend:** $0.00 of the $0.00 authority. No container, no DTU, no eval, no API
spend. Pure shell/test change, as the goal's arithmetic (`0 runs × 0 arms × $0
/ 1.00 = $0.00`) predicted. No infrastructure was registered, claimed, or torn
down; the live ledger was never written to.

---

## The root cause, measured — and it is ONE cause

Both sub-findings are the same defect wearing two hats: **nothing reconciles a
lane's identity across the three files that describe it**, so a lane-name
lookup silently returns "nothing" instead of "I could not tell".

Measured in this batch's own files (`/home/bkrabach/dev/hw-model-performance`):

| file | lane column holds | example |
|---|---|---|
| `manifest.tsv` | LONG lane name | `drbf-compaction-notice-ab` |
| `infra.owners.tsv` | **either** | `drbf-compaction-notice-ab` *and* `vbs`, `127`, `3d2`, `6da`, `k64` |
| `infra.tsv` (`id`) | SHORT work-item id | `val-drbf-a` |

**5 of the 13 distinct owner values are short work-item ids; the other 8 are
long manifest names.** `lane_teardown.sh`'s own usage text documents the short
form ("LANE — SHORT lane / work-item id … NOT the lane directory name"), but
lanes claim with whichever name they happen to hold. Both conventions are live
in one ledger, and nothing declares which is correct.

### Sub-finding 1, traced through the code

`lane_teardown.sh <batch> drbf teardown --yes` exited 0 saying "owns no open
rows" while six rows were open. The path, exactly:

1. rows are `val-drbf-a … val-drbf-c2`, claimed to `drbf-compaction-notice-ab`
   (`infra.owners.tsv` lines 35–40, still on disk);
2. `classify()` (line 137) consults **claims first**:
   `claimed_lane("val-drbf-a")` → `drbf-compaction-notice-ab`, which `!= "drbf"`,
   so it returns `OTHER:drbf-compaction-notice-ab`;
3. the **inference** rule on line 144 — `val-$LANE-*`, which *would* have matched
   `val-drbf-a` for lane `drbf` — is unreachable, because the claim branch
   already returned;
4. every candidate lands in `prot_ids`, `sel_ids` is empty, and line 302 prints
   a success message and exits 0.

The guard was working exactly as designed. The design simply had no way to say
"you named something that nearly matches".

### Sub-finding 2, traced through the code

`highway_status.sh` computes lane liveness (`tmux has-session`, line 64) and
`infra_ledger.sh list` reports open rows, and **no code path reads both**. The
manager found the six orphaned containers by running `incus list` by hand.

### One fix or two? — **One cause, two sites, and they must ship separately.**

The goal asks this be argued from the code. The cause is single (above). The
*blast radii* are not, and that decides the shape:

- **`lane_teardown.sh` DESTROYS.** Its failure mode is silence on the emergency
  path. Its fix must therefore make a near miss **loud and non-zero** — and must
  be conservative, because a guard that fires too widely makes every clean
  lane's routine teardown look broken.
- **`highway_status.sh` only REPORTS.** Its failure mode is invisibility. Its
  fix can afford to over-report, because the cost of a false positive is a
  printed line, not a destroyed container.

They are the same join written twice on purpose: one fails **closed** (refuse,
name the candidates), one fails **open** (report, name the reason). Merging them
into a single mechanism would force one of those two policies onto the wrong
side. That is why this note ships one fix here and one as a patch, rather than a
shared helper.

---

## Deliverables

### 1. A near-miss lane name FAILS LOUDLY — **DONE in substance, NOT LANDABLE IN THIS REPO**

Implemented, proven, and shipped as a patch. See "Goal defect" below for why it
could not be committed as code.

- Patch: `PROPOSED-evals-lane-teardown-near-miss.patch` (applies cleanly with `patch -p0` to
  upstream sha256 `40e6ee93575a784c34a24a9b91b0c871c975d1cc822923ad5e9a88c8f774cd9d`;
  verified by `--dry-run`)
- Harness: `test_lane_teardown_near_miss.sh` (takes the script under test as an
  argument, runs against a throwaway batch in `$TMPDIR`, stubs the DTU CLI
  through the script's own `AMPLIFIER_DT_CLI` seam)
- Transcript: `evidence/00-near-miss-fail-before.txt`, `evidence/01-near-miss-pass-after.txt`

**Fail-before (unpatched): 3 expectations unmet.** **Pass-after: all 13 pass.**

A near miss is defined narrowly — an id that inference *would* have selected
(`val-$LANE`, `val-$LANE-*`), or an owner name prefix-related to `$LANE` on a
token boundary **in either direction**. It exits **4** and prints each candidate
with its open-row count and the exact corrected command. It is deliberately NOT
"any protected row is an error": most lanes provision nothing and run teardown
anyway, so failing them because an unrelated lane holds rows would be a
regression. CASE 4 of the harness pins that.

### 2. An exact lane name still works; `protected-untouched` still protects — **DONE**

Pinned in the same harness against the **patched** script, reproducing the
incident's own recovery transcript byte-for-byte in the numbers that matter:

```
verified-gone=6 rows-flipped=6 ... protected-untouched=4
```

CASE 3 additionally asserts each of the four live-lane containers **still
exists** in the stub's state dir — observable, not inferred from an exit code.

### 3. Orphaned rows are surfaced — **DONE, landed here**

`highway_status.sh` now joins lane liveness to row ownership and reports:

```
SUMMARY batch=… live=1 ended=1 … watchdog=DEAD orphan_rows=6
WARNING: orphan_rows=6 owned by: drbf-compaction-notice-ab(6)
  Open infra-ledger rows whose owning lane is NOT live - infrastructure
  with nothing driving it (Rule 14). Nothing was destroyed; reclaim each
  with: lane_teardown.sh <BATCH_DIR> <lane> teardown --yes
```

and in `HIGHWAY_JSON=1` mode: `"orphan_rows":6,"orphan_owners":"drbf-compaction-notice-ab(6)"`.

**Reporting only. Reaping was deliberately not implemented** — the goal names it
as the riskier choice, and `test_reporting_destroys_nothing` proves inertness
with a `touch <sentinel>` destroy_cmd plus a byte-identical ledger, rather than
inferring it from an exit code.

**Replayed against the real incident data.** A copy of the live batch's
`manifest.tsv` / `infra.tsv` / `infra.owners.tsv`, with drbf's six rows set back
to `open`:

```
{"batch":"replay","live":1,"ended":147,…,"orphan_rows":6,
 "orphan_owners":"drbf-compaction-notice-ab(6)"}
```

The live batch today reports `orphan_rows:0`, matching its zero open rows.

Owner resolution is exact-name, else a **unique** token-boundary prefix, so the
five short-id owners resolve to their lanes. Without that, an exact-match join
would have false-alarmed on ~40% of this batch's owners — and a report that
cries wolf is a report nobody reads. Ambiguous (`l1` against `l1-alpha` and
`l1-beta`), unresolvable, and unclaimed owners are each reported **as such**
rather than guessed at.

### 4. A live lane's rows are NEVER touched by either change — **DONE**

The goal flags this as the deliverable that can be faked by making everything
pass. Pinned on both sides, with a live lane and a dead lane holding rows
**simultaneously**:

- `test_a_live_lanes_rows_are_never_reported_as_orphaned` — live lane owns 2
  rows, dead lane owns 6; asserts `orphan_rows == 6` **and** that the live
  lane's name does not appear in the owner list. Counting every open row would
  also satisfy the incident test; this is the case that separates a real join
  from a row count.
- `test_real_tmux_agrees_with_the_stub` — the same scenario against a **real
  tmux server** on a private socket, then kills the session mid-test and asserts
  the SAME batch flips 1 → 2 orphans with nothing else changed. The stub cannot
  hide an integration break.
- Harness CASE 3 — the live lanes' containers survive an exact-name teardown.

### 5. Full suite green — **DONE, with one pre-existing failure that is not mine**

```
1 failed, 1818 passed, 2 skipped, 13 deselected, 1 xfailed in 14.45s
```

The failure is `tests/test_fail_loud_bundle_activation.py::…::test_error_is_a_bundle_error`
(`assert issubclass(ModuleActivationError, BundleError)`), a foundation-contract
assertion. **Proven pre-existing**: `git stash -u` → the identical failure on
clean HEAD (`1 failed, 1805 passed`). The delta is exactly my +13 tests.

**Two corrections to the goal's KNOWN section**, both cheap to verify:
- The failure it predicted (`test_truststore_wrap_bio_shim.py::test_real_truststore_is_covered_at_cli_import`)
  did **not** occur, in either the clean or the changed run. That prediction is stale.
- The failure that *does* occur is a different one, and the goal does not
  mention it. A later lane should not absorb it either.

`ruff check` and `ruff format` clean on the new test file.

---

## Goal defect (reported, per the goal's own instruction)

**Deliverables 1 and 2 have no target inside the paths this lane owns.**

`lane_teardown.sh` lives at
`/home/bkrabach/dev/openai-evals-team-ci/.amplifier/evaluation/tools/lane_teardown.sh`
— a different repo, and **untracked even there**:

```
$ git -C /home/bkrabach/dev/openai-evals-team-ci ls-files --error-unmatch \
      .amplifier/evaluation/tools/lane_teardown.sh
error: pathspec '…' did not match any file(s) known to git
```

This lane's worktree is `amplifier-app-cli`, which ships
`infra_ledger.sh`, `highway_status.sh`, `launch_lane.sh`, `verify_lane.sh`,
`highway_watchdog.sh` — and **no** `lane_teardown.sh`. Per the goal's own rule
("If the only way to satisfy a deliverable is to write a file outside your
worktree … that is a DEFECT IN THIS GOAL, not a task. Report it against the
goal, ship the patch as an artifact under your ARTIFACT ROOT, and resolve"), I
did exactly that: the fix is implemented and proven, shipped as a patch, and the
other repo was not touched.

**The deeper defect this is a symptom of** — and lane 2nz already flagged it
(its DONE-NOTE, finding 3): the shipped skill **names a tool it does not
ship**. `infra_ledger.sh` tells the operator, in its refusal message, to run
`.amplifier/evaluation/tools/lane_teardown.sh` — an evals-repo path — and
`SKILL.md` names the tool with no path at all. So the shipped skill's
documented recovery path points at an untracked file in a repo the skill knows
nothing about, and that file is where the emergency footgun lives. Until
`lane_teardown.sh` is adopted into this repo (or the skill stops naming it),
every fix to it is unversioned and every lane that finds a bug in it hits this
same wall.

**Recommended next item:** adopt `lane_teardown.sh` into
`amplifier_app_cli/data/skills/ten-lane-highway/scripts/`, with the near-miss
patch applied and this harness converted to a pytest file. That is a
deliberate scope decision, not something to smuggle in here: it would create a
second copy of a 409-line tool the manager runs live from the other path, and a
divergence there is worse than the footgun.

---

## Deviations and choices

- **No reaping.** Reporting only, as the goal permits and prefers. Recorded here
  as a choice, not an omission.
- **tmux is stubbed** in most tests, through a `PATH` shim, for the same reason
  `lane_teardown.sh` indirects the DTU CLI: the subject is the join, not the
  multiplexer. One test runs against real tmux so the stub cannot hide a break.
- **Environments are built explicitly** in every subprocess call
  (`model_performance-etuz`): a lane exports `HIGHWAY_TMUX_SOCKET`, so an
  inherited env would make these pass in CI and fail inside every lane.
- **`skipif` with a stated reason** on Windows / no-bash, matching the precedent
  set on #306 and #310 — these scripts are GNU/Linux-only by construction.
- **Never ran `sweep`**, never ran a teardown against the live ledger, and never
  wrote to `/home/bkrabach/dev/hw-model-performance`. The incident replay used a
  `cp` of its three state files into `$TMPDIR`.

## Files

| path | what |
|---|---|
| `amplifier_app_cli/data/skills/ten-lane-highway/scripts/highway_status.sh` | the orphan-row join (+95 lines) |
| `amplifier_app_cli/data/skills/ten-lane-highway/SKILL.md` | documents `orphan_rows`, per 0rg's guard-and-docs-together precedent |
| `tests/test_ten_lane_highway_orphan_rows.py` | 13 tests |
| `docs/lanes/ye80b-teardown-orphan-rows/PROPOSED-evals-lane-teardown-near-miss.patch` | sub-finding 1's fix |
| `docs/lanes/ye80b-teardown-orphan-rows/test_lane_teardown_near_miss.sh` | its proof harness |
| `docs/lanes/ye80b-teardown-orphan-rows/evidence/` | fail-before / pass-after transcripts, incident replay, both suite runs, lint |
