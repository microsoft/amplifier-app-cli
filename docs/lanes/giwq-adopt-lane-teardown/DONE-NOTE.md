# Lane `giwq` — the shipped skill now ships its own emergency recovery path

**Item:** `model_performance-giwq` (project `model_performance`)
**Repo:** `microsoft/amplifier-app-cli`, branch `lane/giwq-adopt-lane-teardown`
**Outcome:** **A. RESOLVED** — every deliverable DONE. No cap-bound
NOT-POSSIBLE, no blocker.
**Spend:** **$0.00** against a $0.00 authority. No container, no DTU, no API
call, no infrastructure registered — pure shell/test/docs work, exactly as the
goal priced it. Nothing to tear down; `lane_teardown.sh` was not run against
this batch's live ledger and `infra_ledger.sh sweep` was never run.

---

## The defect, in one line

The skill's own refusal message told an operator, mid-incident, to run
`.amplifier/evaluation/tools/lane_teardown.sh` — a path in a **different repo**,
to a file **untracked even there**:

```
$ git -C ~/dev/openai-evals-team-ci ls-files --error-unmatch .amplifier/evaluation/tools/lane_teardown.sh
error: pathspec '.amplifier/evaluation/tools/lane_teardown.sh' did not match any file(s) known to git
```

That is the **only** lane-scoped teardown path. The alternative, `sweep`, is the
manager's batch-close verb that destroys **every** lane's infrastructure.

---

## Deliverables

| # | Deliverable | State |
|---|---|---|
| 1 | `lane_teardown.sh` adopted into the shipped skill; `infra_ledger.sh` + `SKILL.md` name an in-repo path | **DONE** |
| 2 | ye80b's near-miss patch applied to the adopted copy, pinned by a pytest converted from the 13-assertion harness | **DONE** |
| 3 | Exactly ONE implementation | **DONE (shipped copy authoritative; evals-repo action stated + shim written)** |
| 4 | Adoption did not widen what teardown can destroy — proven with tests | **DONE** |
| 5 | Fail-before for the near-miss behaviour, quoted | **DONE** |
| 6 | Full suite green, pasted in the PR body; DRAFT PR; not merged | **DONE** |
| 7 | DONE-NOTE at `docs/lanes/giwq-adopt-lane-teardown/DONE-NOTE.md` | **DONE (this file)** |

---

### 1. Adoption

`amplifier_app_cli/data/skills/ten-lane-highway/scripts/lane_teardown.sh` — the
evals copy, verbatim, plus ye80b's patch and three header edits:

* a **WHERE THIS LIVES** block recording that this file is now the one
  implementation and how to resolve it (`<skill_directory>/scripts/…`);
* an **EXIT CODES** block, because the near-miss guard introduces a **4** that
  callers must be able to read;
* `usage()` derives the help text from the file (`sed -n '2,/^set -uo
  pipefail/p'`) instead of the hardcoded `'2,72p'` range — which had **already
  drifted past the end of the header**, so every header edit silently truncated
  or over-ran the help. Verified: 99 lines of help, ending on the last comment.

Three printed self-references (`audit`'s reconcile hint, the near-miss guard's
two re-run suggestions) now use `$0`, so what an operator is told to run is a
real path rather than a bare name that is on no `PATH`.

**Callers now name a path that exists:**

| File | Before | After |
|---|---|---|
| `infra_ledger.sh` (sweep refusal, stderr) | `.amplifier/evaluation/tools/lane_teardown.sh …` | `$LANE_TEARDOWN …`, resolved from `BASH_SOURCE` — the sibling in the same `scripts/` dir |
| `infra_ledger.sh` (header + guard comment) | same cross-repo path | `<skill_directory>/scripts/lane_teardown.sh`; the old path survives **only** in a comment explaining why it changed |
| `SKILL.md` | `lane_teardown.sh …`, no path | `<skill_directory>/scripts/lane_teardown.sh …`, plus a row in the instruments table and the near-miss behaviour documented |
| `highway_status.sh` (orphan-row reclaim hint) | bare `lane_teardown.sh …` | path derived from `BASH_SOURCE`, copy-pasteable |

`highway_status.sh` was **not** named in the goal's deliverable, but it prints
the *same* reclaim instruction for the *same* 2026-09-05 incident and carried
the *same* defect in its "no path at all" form. Fixing one and leaving the other
would have left the incident's own recovery instruction unrunnable. It is a
one-line change to an `echo`; PR #306 is open against `highway_watchdog.sh`, a
**different file**, which this lane did not touch.

### 2. The near-miss guard

ye80b's patch applied with `patch -p4`, clean, no fuzz. The behaviour: a lane
name that almost matches an owner **exits 4 naming each candidate owner and its
open-row count**, instead of printing success for a teardown that would do
nothing.

Converted `docs/lanes/ye80b-teardown-orphan-rows/test_lane_teardown_near_miss.sh`
(13 assertions, 5 cases) into `tests/test_ten_lane_highway_lane_teardown.py`
(**14 tests**). The harness's 5 cases became tests 1–7; tests 8–14 are the
adoption-safety proofs below.

Two standards followed deliberately:

* **Every "ran nothing" claim is observable.** A state file that still exists, or
  a `touch` sentinel that does not — never an exit code. The buggy path *prints
  a success message*, so an exit code alone cannot tell "refused" from "ran and
  failed" (the `test_ten_lane_highway_infra_ledger.py` standard).
* **The subprocess environment is built explicitly.** `model_performance-etuz`
  was this same mistake in this same directory — green in CI, red inside every
  lane. `AMPLIFIER_DT_CLI` and `DT_STATE` are stripped from the inherited
  environment and set deliberately (see `569c9b8`).

Platform guard: `sys.platform != "linux"`, with the reason stated —
`lane_teardown.sh` uses `flock` (util-linux, absent on stock macOS) and `chmod
--reference` (a GNU flag with no BSD equivalent). Same precedent that cleared
#310 and #313.

### 3. Exactly one implementation — what was chosen, and why

**The shipped copy is authoritative.** This lane owns `amplifier-app-cli` only;
the evals repo is a different repo and the goal forbids touching it. So the
evals-side action is *stated*, and the file it needs is *written*, but not
installed:

**What must happen in `openai-evals-team-ci`:** replace
`.amplifier/evaluation/tools/lane_teardown.sh` with the **thin shim** at
`docs/lanes/giwq-adopt-lane-teardown/PROPOSED-evals-lane-teardown-shim.sh`, and
`git add` it — the current file is untracked, so today the evals repo has no
record of it at all.

**Shim, not deletion**, because every operator instruction written before
2026-09-06 — *including this batch's own `GOAL.md` files, already handed to
running lanes* — spells the old path. Deleting the file turns each of those into
`No such file or directory` at exactly the moment an operator is mid-incident.
Delete it once no live instruction names it.

The shim resolves the shipped script in three steps — `$LANE_TEARDOWN_SH`, then
derivation from the installed `amplifier` entry point, then a sibling dev
checkout — and **never falls back to a local implementation**. A fallback *is* a
second implementation, and an out-of-date one would reintroduce precisely the
near-miss footgun. Not found ⇒ exit 127 naming the fix. Verified on this host:

```
$ bash PROPOSED-evals-lane-teardown-shim.sh /tmp/no-such-batch zzz list
ERROR: lane_teardown.sh now ships WITH the ten-lane-highway skill, and this
       machine has no copy of it. Refusing to run an unpinned teardown.
```
(correct today — the installed CLI predates this PR)

```
$ LANE_TEARDOWN_SH=<this repo>/…/scripts/lane_teardown.sh bash …shim.sh /tmp/no-such-batch zzz list
LANE-TEARDOWN: no ledger (/tmp/no-such-batch/infra.tsv) — nothing to do
```

and the step-2 derivation resolves correctly on this host once the CLI carries
the file:
`/home/bkrabach/.local/share/uv/tools/amplifier/lib/python3.13/site-packages/amplifier_app_cli/data/skills/ten-lane-highway/scripts/`.

**Until the evals repo adopts the shim, two copies exist.** That is the one
thing this item cannot close from inside this repo, and it is stated here rather
than implied.

### 4. Adoption did not widen what teardown can destroy

The deliverable most easily faked by making everything pass, so each proof is
observable:

| Test | Proves |
|---|---|
| `test_lane_teardown_has_no_sweep_verb` | `sweep` is rejected as an unknown command; the row's `touch` sentinel **does not exist** afterwards |
| `test_lane_teardown_offers_no_all_owners_escape_hatch` | `--all-owners`, `--all`, `--everything` each refused as an unknown option, sentinel absent; and the string `--all-owners` appears **0 times** in the script |
| `test_the_multi_owner_sweep_refusal_still_refuses` | 0rg's guard, re-run post-adoption: exit 3, **both** sentinels absent |
| `test_a_live_lanes_rows_are_never_touched` | `protected-untouched=4`, and the four live containers **still exist** — a live lane and a dead lane holding rows simultaneously, the shape the goal asked for |
| `test_reconcile_still_refuses_to_reclaim_a_live_row` | `reconcile` runs no destroy command and refuses a PRESENT container |
| `test_the_sweep_refusal_names_a_lane_teardown_path_that_actually_exists` | the path the refusal **actually prints** is `stat`-ed and resolved — asserting on the string would have passed against the original defect |
| `test_no_shipped_instruction_sends_an_operator_into_another_repo` | the cross-repo path appears in no `SKILL.md` text and, in the scripts, only on comment lines |

### 5. Fail-before

**Shell harness, unpatched upstream script** — the shape ye80b measured,
reproduced exactly:

```
CASE 1 — the incident: a NEAR-MISS lane name must FAIL LOUDLY
  FAIL  exited 0 on a near-miss name (the footgun: reports success, does nothing)
  FAIL  did not name the candidate lane — an operator cannot act on this
  PASS  destroyed nothing and flipped no row while refusing
…
CASE 5 — the MIRROR near miss: long name typed, rows claimed short
  FAIL  mirror near miss exited 0 without naming a candidate
==================================================
RESULT: FAIL — 3 expectation(s) unmet
```

**New pytest, adopted file swapped for the unpatched upstream copy** — the same
three, and no others:

```
FAILED …::test_near_miss_lane_name_refuses_instead_of_reporting_success
FAILED …::test_near_miss_refusal_names_the_candidate_lane_and_its_open_row_count
FAILED …::test_the_mirror_near_miss_long_name_typed_rows_claimed_short
3 failed, 11 passed
```

**New pytest against the full pre-adoption state** (script absent, docs at
`origin/main`) — `12 failed, 2 passed`, including the two path deliverables.

**After:** shell harness `RESULT: PASS`; pytest `14 passed`.

Transcripts: `docs/lanes/giwq-adopt-lane-teardown/evidence/00…05`.

---

## Findings

1. **`usage()`'s line range had already drifted.** `sed -n '2,72p'` was pinned to
   a header that has grown since; the help text was silently truncated mid-topic
   and nothing could notice. Now derived from the file. A hardcoded line range
   into your own source is a comment that lies as soon as anyone edits above it.

2. **The full suite failed once, then did not.** The first full run reported
   `1 failed` — `tests/test_truststore_wrap_bio_shim.py::test_real_truststore_is_covered_at_cli_import`,
   with `SHIM_STATUS = 'skipped: truststore is not importable'` — which is the
   failure the goal warned to expect. It did **not** reproduce: **four**
   subsequent full runs were green (1894 passed), including one with an extra
   throwaway test file to perturb collection order, and the `origin/main`
   baseline was green too (1880 passed). The one observed failure was in the run
   immediately following venv creation, which is the likeliest cause. Reported
   as observed-once-not-reproduced rather than quietly dropped **or** claimed as
   pre-existing — the goal's "known pre-existing full-suite failure" did not
   hold on this checkout, and a later lane should not inherit that belief
   unexamined.

3. **The near-miss guard is narrow on purpose, and that is testable.** "Any
   protected row is an error" would fail every clean lane that provisioned
   nothing. `test_a_genuinely_idle_lane_still_exits_zero` pins that, and it
   passes against the unpatched script too — a false-failure regression would
   show up as *that* test breaking, not as a vague loss of confidence.

## What remains open

* **The evals repo still holds its own untracked copy.** One file to install
  (`PROPOSED-evals-lane-teardown-shim.sh`) plus a `git add`. Until then, two
  implementations exist and can drift.
* **This PR is a DRAFT and was not merged.** The shipped skill only ships the
  recovery path once it lands and the CLI is reinstalled; the shim's step-2
  derivation stays unresolved until then (it refuses loudly, by design).

## Spend ledger

| Item | Amount |
|---|---|
| Authority | **$0.00** (0 runs × 0 arms × $0 / 1.00) |
| Spent | **$0.00** |
| Residue | $0.00 — nothing to buy; the goal's arithmetic closed on first read |
| Infrastructure registered | **none** — `infra_ledger.sh add` not run, `lane_teardown.sh claim/teardown` not run against this batch, `sweep` never run |
