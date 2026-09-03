# Lane 2nz — upstream the ten-lane-highway skill guards

**Item:** `model_performance-2nz` (project `model_performance`)
**Repo:** `microsoft/amplifier-app-cli`, branch `lane/2nz-upstream-skill-guards`
**Date:** 2026-09-03
**Outcome:** **A. RESOLVED** — every required deliverable DONE; the one
OPTIONAL-IF-CAP-PERMITS deliverable is NOT-POSSIBLE for a scope reason, not a cap reason.

---

## What this item was

Two manager fixes were applied by hand to the **installed** skill directory
(`~/.local/share/uv/tools/amplifier/.../amplifier_app_cli/data/skills/ten-lane-highway`).
An `amplifier` tool update re-installed that directory on 2026-09-03 at 05:54–06:13
and **silently reverted them**. The reverted mechanism (`model_performance-0rg`'s
multi-lane sweep guard) is the thing that stops one lane's `sweep` from destroying
every other lane's DTUs — the failure that cost lanes l1 and 161 three DTUs each,
35 minutes into their measurements, on 2026-09-02.

The fix is to put them where a tool update **delivers** them instead of deleting
them: this repo's shipped source.

---

## Deliverables

### 1. Both guards in this repo's shipped source — **DONE**

`amplifier_app_cli/data/skills/ten-lane-highway/scripts/infra_ledger.sh`.

**Ported, not redesigned.** Method: the installed file was copied over the repo
file **byte-for-byte** (`diff -q` clean immediately after the copy), so the
implementation in the PR is literally the measured one. The only subsequent edit
is the top-of-file usage block, which described a `sweep` that no longer exists —
that is documentation of the ported behaviour, not a change to it. The post-port
diff against the installed copy is exactly that comment block and nothing else.

- **0rg multi-lane guard** (marker `MULTI-LANE GUARD`): `sweep` exits **3** and
  runs **no destroy_cmd** when the open rows span more than one owner *or* any
  row is unattributable. `--all-owners` is the manager's batch-close override.
  Ownership is read from the sibling `infra.owners.tsv` (`ts \t id \t lane`).
- **bqu already-absent handling** (marker `ALREADY_GONE_RE`): a destroy_cmd
  failing with a **narrow** not-found signature closes the row as
  **`swept:already-absent`** — distinct from `swept`. A real failure still exits
  non-zero and leaves the row `open`.

Both markers remain greppable, so the manager's stopgap
`$BATCH_DIR/check_skill_guards.sh` still finds them: run against the repo source
it prints `skill guards OK (ten-lane-highway)`, exit 0.

### 2. SKILL.md updated in the same change — **DONE**

`amplifier_app_cli/data/skills/ten-lane-highway/SKILL.md`, five sites:

- Instruments table entry now reads `sweep --all-owners`, followed by a block
  stating plainly that **`sweep` is the manager's batch-close verb, never a
  lane's**, naming the lane-scoped `lane_teardown.sh` as the alternative, and
  documenting `swept:already-absent`.
- Phase 2 (goal-file composition): a goal file must never tell a lane to run
  `sweep`.
- Phase 7 close instruction: `sweep --all-owners`.
- Phase 7 success criteria: `sweep --all-owners`.
- Rule 14: `sweep --all-owners`, plus the manager-verb/lane-tool split.

**Note this could not be "ported":** the `--all-owners` documentation was *also*
reverted and was **never re-applied to the installed copy** — `grep -c "all-owners"`
is **0** in both the installed SKILL.md and this repo's pre-change SKILL.md. It was
authored fresh here to 0rg's stated requirement (guard + docs land together, because
a guard that deadlocks the documented close is a regression).

### 3. Tests in this repo's own suite — **DONE**

`tests/test_ten_lane_highway_infra_ledger.py` — 12 tests, all five measured cases
plus regression coverage:

| # | Case | Test |
|---|---|---|
| 1 | already-gone closes, exit 0, `swept:already-absent` | `test_case1_already_gone_closes_row_as_already_absent` |
| 2 | REAL failure exits non-zero, row stays `open` | `test_case2_real_failure_exits_nonzero_and_leaves_row_open` |
| 3 | genuine teardown records `swept` | `test_case3_genuine_teardown_records_swept`, `test_case3_mixed_outcomes_are_recorded_distinctly` |
| 4 | multi-owner without the flag: exit 3, ran nothing | `test_case4_multi_owner_sweep_refuses_and_runs_nothing`, `test_case4_unattributed_rows_also_refuse` |
| 5 | `--all-owners` proceeds | `test_case5_all_owners_proceeds` |

Plus `test_single_owner_sweep_is_allowed_without_the_flag` (the guard fires on
ambiguity, not on sweeping), `test_sweep_is_idempotent` (closed rows — including
`swept:already-absent` — are never re-run, so the fix does not merely move the
deadlock), `test_skill_md_documents_the_manager_override`, and two
`test_guard_markers_are_greppable` cases pinning the drift-check markers.

**Observable destroy_cmd, as required:** every guard case uses `touch <sentinel>`
and asserts the sentinel's **absence**. "Ran nothing" is proven, not inferred —
an exit code alone cannot distinguish *refused before acting* from *acted and then
failed*.

**Discriminating evidence (the tests are not vacuous):** run against the
pre-port script (`git checkout -- infra_ledger.sh`), **7 of 12 fail** — both
marker tests, case 1, case 3-mixed, both case-4 tests, and idempotence. Against
the ported script, 12/12 pass. Cases 2, 3 and 5 pass on both by design: they are
regression guards on behaviour that must *not* change.

Windows: module-level skip (POSIX shell script; `bash`/`mktemp`/`awk`), following
the precedent already set by this repo's pty tests.

### 4. Has anything ELSE in this skill dir drifted? — **DONE. Yes, one more thing, and it is now closed.**

Method: `diff -rq` of this repo's `amplifier_app_cli/data/skills/` against the
installed tree (installed version 0.1.1, same as this checkout, so a version skew
cannot explain a difference).

- **Whole shipped skills tree: exactly ONE file differed** —
  `ten-lane-highway/scripts/infra_ledger.sh`. Every other file in every other
  shipped skill was byte-identical. No further silent manager patches are sitting
  in the installed tree.
- **A second, still-live drift, in the other direction:** `SKILL.md` was
  byte-identical between repo and installed — and `--all-owners` appeared in
  **neither** (`grep -c` = 0 in both). So cycle 49's re-apply restored the *guard*
  but not its *documentation*. Until this PR, the shipped skill would have told a
  manager to close a batch with a bare `sweep`, which the restored guard refuses
  with exit 3 the moment two lanes hold infrastructure — a documented close that
  cannot succeed. Closed here by deliverable 2.
- **`merge_gate.sh` is not in this skill dir in either copy, and never was** —
  see below.

### 5. Full suite green — **DONE**

`uv run pytest -q` → **1682 passed, 1 skipped, 13 deselected, 1 xfailed in 12.05s**.
Pasted in the PR body.

### 6. `tj2`'s `merge_gate.sh` fix (OPTIONAL-IF-CAP-PERMITS) — **NOT-POSSIBLE (scope, not cap)**

**What was executed:** located the file, confirmed its repo, confirmed this repo
never ships it. `merge_gate.sh` exists at
`/home/bkrabach/dev/openai-evals-team-ci/.amplifier/evaluation/tools/merge_gate.sh`
(11,553 bytes, mtime 2026-09-02 18:29), alongside `test_merge_gate.sh` and
`README-merge-gate.md`. It is a tool of the **openai-evals-team-ci** repo, not a
ten-lane-highway skill script: it is absent from `data/skills/ten-lane-highway/scripts/`
in **both** this repo and the installed copy, so nothing was reverted from here and
there is nothing here to upstream.

**Reason it is NOT-POSSIBLE in this lane:** it lives in another repo, and this
lane's scope-outs forbid touching other repos. It is not blocked by the cap
(the cap is $0 and nothing here costs money) and not blocked by inability to
find it. **Recommended follow-up:** a separate item against
`openai-evals-team-ci` — its tools directory is version-controlled by that repo,
so tj2's fix is durable there already *if it is committed*; that is the thing worth
checking, and it is checkable at zero cost.

### 7. Draft PR + this note — **DONE**

**Draft PR: [microsoft/amplifier-app-cli#299](https://github.com/microsoft/amplifier-app-cli/pull/299)** on branch
`lane/2nz-upstream-skill-guards`. Publication values in `DONE.json` were read back from
the remote with `publication_readback.sh` (`git ls-remote` + `gh pr list`), not typed
from a local `git log`.

---

## Spend

**Authority: $0.00 — arithmetic `0 runs × 0 arms × $0.00 / 1.00 valid = $0.00`, slack $0.00.**

**The arithmetic closes, and it closes trivially.** This deliverable is a pure
source change: no model runs, no arms, no validity rate to divide by. There is no
smallest-indivisible-purchase problem here — the deliverable's price is genuinely
zero, so a $0 authority funds 100% of it. (Contrast lane 1ru, whose authority was
sized for 2 valid runs against a goal asking for 4; nothing of that shape applies.)

- **API / DTU spend: $0.00.** No DTU, gitea instance, container, service or
  background process was created. **Nothing registered in the infra ledger; nothing
  to tear down.** `infra_ledger.sh ... sweep` was never run against a real batch
  ledger — every sweep in this lane ran against throwaway ledgers in pytest
  `tmp_path` directories.
- Agent tokens for this lane are outside the item's $0 API/DTU authority and were
  not separately metered.

---

## Deviations and judgment calls

1. **The SKILL.md `--all-owners` text is authored, not ported.** There was nothing
   to port — it was 0 in both copies (see deliverable 4). "Do NOT redesign" was
   honoured for the two *guards*, which were copied byte-for-byte; it cannot apply
   to prose that does not exist anywhere. Recorded rather than silently absorbed.
2. **The script's usage header was rewritten.** It documented a `sweep` with no
   `--all-owners` and no `swept:already-absent`. Leaving it would have shipped a
   file that contradicts itself. This is the only post-copy edit to the script and
   it touches comments only.
3. **`lane_teardown.sh` is named in SKILL.md without a repo-relative path.** The
   ported guard's own error message hard-codes
   `.amplifier/evaluation/tools/lane_teardown.sh`, which is an evals-repo path;
   SKILL.md ships to every consumer of this skill, so it names the tool by name and
   role ("the batch's lane-scoped teardown tool") rather than by a path that will
   not exist in most batches. The guard's message was left verbatim as measured.
4. **Windows skip on the new test module.** Chosen over a CI deselect, per this
   repo's own CI comment ("an excluded test is a test nobody is watching").
5. **Artifact root is `ai_working/2nz-upstream-skill-guards/`, not the
   `docs/lanes/…` the goal named.** The goal text asserted
   `ai_working/2nz-upstream-skill-guards/DONE-NOTE.md` "resolved for THIS repo by
   artifact-path/v1" and cited `check_lane_artifact_paths.py` as the checker. Run,
   that checker resolves this repo to **`ai_working/<lane>/` [R2 ai_working/]** —
   `ai_working/` is tracked at the base ref, so R2 fires and the `docs/lanes/`
   fallback (R3) never applies. The checker's own docstring lists
   `ai_working/<lane>/  4 lanes  (amplifier-app-cli)` as this repo's convention and
   groups `docs/<something>/<lane>/` among the *inconsistent* placements the rule
   exists to eliminate. This repo's `ai_working/` already holds four prior lanes.
   The goal's stated path was therefore a mis-resolution; the enforced rule wins,
   and the note is written where the check passes. Recorded here rather than
   absorbed, per "choose, record the choice, continue".
6. **No `BLOCKED.md`.** Outcome branch A; the item resolves.

---

## The finding worth keeping

The revert itself is the signal, and it is now recorded in three places that a
tool update cannot erase: the shipped source, a test that fails without it, and
this note.

But note what the diff caught that the re-apply did not: **the guard came back
without its documentation.** A partial re-apply is *more* dangerous than a total
one, because the surviving half looks like the whole thing. `check_skill_guards.sh`
greps only for the two code markers and would have reported `skill guards OK`
against a SKILL.md that still told the manager to run a `sweep` the guard refuses.
Worth extending that check with a third assertion — `--all-owners` present in
SKILL.md — so the docs half is watched too.
