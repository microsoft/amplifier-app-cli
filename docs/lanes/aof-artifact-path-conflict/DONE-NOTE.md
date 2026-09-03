# DONE-NOTE — `model_performance-aof` · lane `aof-artifact-path-conflict`

> **STATUS: the item was RESOLVED and then REOPENED by this lane, as a
> self-correction.** The work below is complete, published and measured, but the
> checker fix lives on an unmerged branch in the evaluation repo — so the guard
> is **not live for future lanes**, and this item's own acceptance criterion
> ("a lane … runs `check_lane_artifact_paths.py` … the checker agrees") is not
> yet satisfied against the copy a lane actually runs. Resolving while my own
> resolution said "needs a local merge to take effect" was an overclaim.
> Nothing about the work changed; only the claim of completion. The one
> remaining action is a local merge, which this goal forbids a lane to perform —
> see **Honest limits** for the command and its revert.

**The decision: option (a). The goal template wins.** `check_lane_artifact_paths.py`
now resolves `amplifier-app-cli` to `docs/lanes/<lane>/` by declaration, not by
inference. **This note is at `docs/lanes/aof-artifact-path-conflict/DONE-NOTE.md`,
the path both sides now name** — the first lane in this repo for which that is
unambiguous.

Spend: **$0.00** against a $0.00 authority (`0 runs × 0 arms × $0 / 1.00 = $0.00`).
No API calls, no DTU, no infrastructure created or claimed. The authority's
arithmetic closes: this was a pure source change and nothing needed buying.

---

## Which option, and why — argued from the code

**(a), and (b) turns out not to be the cheap option it looks like.** The two
"sides" are not independent: `tools/rewrite_goal_artifact_paths.py:49` imports
`resolve_artifact_dir` **from the checker** and generates each goal's ARTIFACT
ROOT sentence from it — its own docstring says this is deliberate, "so the
instruction and the check can never disagree." So the per-repo goal text is
*derived from the checker*, and the only edit that moves both sides at once is
an edit to the checker. Under (b) you would still have to change the resolver
(so the generator emits `ai_working/`), **plus** change the hand-written CODE
goal template — which lives in the manager's per-batch `goals/` directory,
outside any git repo, with no test harness. The deliverable that stops the
recurrence is *a test*, and under (b) there is nowhere for it to live. (b) is
not cheaper; it is (a) with the answer inverted and the guard dropped.

Two facts then decide which answer is right, rather than which is convenient:

1. **R2 fires for this repo because a lane artifact created the directory.**
   The first tracked path under `ai_working/` is
   `ai_working/3yc-timedout-session-resumable/DONE-NOTE.md` (`0d93352`,
   2026-09-02) — a lane's own note. At the base ref `35ab604`, *all five*
   tracked paths under `ai_working/` are lane artifacts; the repo tracks no
   content there at any ref, and its `.gitignore` mentions only
   `ai_working/tmp`. The rule was reading its own past output back as evidence
   of a repo convention — a feedback loop, not a convention.
2. **The repo's own recent merged history says `docs/lanes/`.** Four
   consecutive merged lanes placed their notes there: n1i (#292), 9w0 (#297),
   eem (#298), 2nz (#299). All four were graded VIOLATION by the checker, and
   `2nz` paid the churn twice — it wrote its note to `ai_working/` on the
   checker's say-so, then deleted it and rewrote it under `docs/lanes/`
   (`c80e04e`).

`docs/lanes/` is also a subdirectory of a real documentation tree a reader
already looks in; `ai_working/` is a scratch directory this repo partially
gitignores.

---

## What changed

**In the evaluation repo** (`.amplifier/evaluation`), branch
`lane/aof-artifact-path-conflict`, commit `449ed59`, base `6269c44`:

* `tools/check_lane_artifact_paths.py` — a new **R-PIN** rule, checked before
  R0–R3:

  ```python
  PINNED_ARTIFACT_ROOTS: dict[str, tuple[str, tuple[str, ...]]] = {
      "amplifier-app-cli": ("docs/lanes", ("ai_working",)),
  }
  ```

  Keyed on the checkout directory name **and** the `origin` URL's repo slug, so
  the pin does not silently stop applying to a checkout cloned under another
  name. `legacy_roots` is retrospective only — `dir_for()` never returns one, so
  no new lane is ever sent to `ai_working/`, while a lane that already landed
  there stays COMPLIANT.
* `scenarios/_harness/tests/test_artifact_path_resolution.py` — 10 tests.
  Suite **233 → 243 passed**.

**In this repo**: `docs/lanes/README.md` (the human-readable half of the same
statement) and this lane's own directory. **No app-cli source changed** — full
suite `1682 passed, 1 skipped, 13 deselected, 1 xfailed`, byte-identical to the
`35ab604` baseline.

### Deviation, stated plainly

The goal launched this as a CODE lane with an `amplifier-app-cli` worktree, but
**both candidate fixes live in the evaluation repo, not in this one** — the
checker is `.amplifier/evaluation/tools/check_lane_artifact_paths.py` and the
goal generator is beside it. Writing only a DONE-NOTE here and calling the item
done would have left the actual conflict in place, so I made the change where
it lives, on a lane branch in a git worktree
(`lanes/aof-artifact-path-conflict/evals-fix/evaluation`), exactly as every
evals lane in this batch does. Nothing was merged; that repo has no remote, so
there is no PR to open on it. The complete patch is committed here as
`PROPOSED-evals-artifact-path-R-PIN.patch` so it is reviewable from the app-cli
PR and survives teardown of the worktree.

The worktree is nested one level down (`evals-fix/evaluation`) on purpose:
`check_lane_artifact_paths.py --lane-dir` picks the first directory under the
lane dir containing a `.git`, so a second checkout placed directly beside
`amplifier-app-cli/` would have made this lane's own grading depend on
`iterdir()` ordering. Verified: `--lane-dir` still resolves to
`amplifier-app-cli`.

---

## Deliverables

| Deliverable | State |
|---|---|
| A goal-conformant DONE-NOTE gets NO VIOLATION | **DEMONSTRATED, NOT YET LIVE** — true of the patched checker and of the merged tree; **not** of `main`, which is what a future lane runs. On **this real lane**: `--strict` exit **1 / VIOLATION** under the parent checker, **0 / COMPLIANT** under the patched one, same command, same directory (`evidence/07-this-lane-graded.txt`). Synthetic control, same script both ways: `evidence/01-fail-before.txt` vs `02-pass-after.txt` |
| A test pinning the resolution for this repo | **WRITTEN AND GREEN, NOT YET LIVE** — `test_pin_survives_every_directory_that_would_have_moved_it` tracks `probes/`, `ai_working/` **and** a three-member R0 wave family at the base ref simultaneously and asserts the answer does not move. It passes on the branch and on the merged tree (`evidence/09`), but it does **not run on future lanes until the branch is merged into evals main**, and a guard that does not run is not a guard. This is the row the item was reopened for |
| The `kez` hazard is still refused | **DONE** — `test_root_done_note_is_still_a_violation` (pinned repo) and `test_root_done_note_is_a_violation_in_an_unpinned_repo_too`. Live confirmation: `adq` is still VIOLATION for `../DONE-NOTE.md` |
| Landed artifacts under BOTH conventions left where they are | **DONE** — nothing relocated. `ai_working/adq-.../DONE-NOTE.md` and `ai_working/9kk-.../DONE-NOTE.md` are reported `ok` under the pin |
| Say which option and why | **DONE** — above |
| Full suite green | **DONE** — app-cli 1682 passed (`evidence/06`); evals 243 passed (`evidence/05`) |
| Fail-before evidence | **DONE** — `evidence/01`, plus the batch-wide before/after diff in `evidence/03` |
| DRAFT PR on origin | **DONE** — see the PR body; the evals half is carried as a patch artifact because that repo has no remote |
| DONE-NOTE follows the convention landed on | **DONE** — `docs/lanes/aof-artifact-path-conflict/DONE-NOTE.md` |

Nothing is NOT-POSSIBLE, and the $0 cap never bound. But two rows above are
GREEN-BUT-NOT-LIVE, and that is why this item was reopened rather than left
closed: the guard only guards once `lane/aof-artifact-path-conflict` is merged
into evals main.

---

## Measured effect, batch-wide

`check_lane_artifact_paths.py --manifest <HW>/manifest.tsv --all --markdown`,
before vs after (`evidence/03-batch-report-diff.txt`):

| | COMPLIANT | VIOLATION |
|---|---:|---:|
| whole batch, before | 58 | 56 |
| whole batch, after | 62 | 52 |
| `amplifier-app-cli`, before | 0 | 7 |
| `amplifier-app-cli`, after | **4** | **3** |

**Only `amplifier-app-cli` rows move.** Every other repo's rule, expected shape
and per-lane status are byte-identical — the diff contains no other repo. That
is the SCOPE-OUT "do not change any other repo's convention", checked rather
than asserted, and it is also pinned by
`test_unpinned_repo_still_infers_from_the_tree`.

The 3 residual app-cli violations are pre-existing and **unrelated to the root
choice** — the pin neither creates nor hides them:

* `adq` — `../DONE-NOTE.md`, written *outside* the repo checkout. This is the
  `kez` shape and is correctly still refused.
* `9kk` — wrote into `ai_working/adq-routing-list-shadowing/`, another lane's
  directory. Its own note is `ok`.
* `3yc` — the manifest lane id is `3yc-timedout-session-not-resumable`; the
  directory is `ai_working/3yc-timedout-session-resumable/` (no "not"). A name
  mismatch, identical before and after.

## Both sides agree — at the generator, not just the checker

`rewrite_goal_artifact_paths.artifact_dir_for()` is the function that writes the
ARTIFACT ROOT sentence into a goal file. For all 8 app-cli lanes in the manifest
it returned `ai_working/<lane>/` before and returns `docs/lanes/<lane>/` after
(`evidence/04-goal-generator-before-after.txt`). The instruction a future
app-cli lane is *given* and the rule it is *graded against* are now the same
string, produced by the same call.

---

## Honest limits

* **The pin is a list, and a list has to be maintained.** Adding a repo to
  `PINNED_ARTIFACT_ROOTS` is a decision someone must make; R0–R3 still guess for
  every repo not in it. That is the trade this item asked for — the defect was
  that the answer was *inferred*, and an inference cannot be made to hold still.
* **`PRE-RULE` can no longer fire for a pinned repo** (it triggers on
  `expected_now != expected`, and a pin makes those equal at every ref).
  `legacy_roots` covers the same ground for `amplifier-app-cli` and is why the
  three `ai_working/` lanes stay COMPLIANT — but a future pin added without a
  `legacy_roots` entry would retroactively fail lanes that obeyed the old
  answer. The docstring says so at the table.
* **The evals-side commit is on a branch in a repo with no remote, and needs a
  local merge to take effect.** That is the one thing outstanding, and it is not
  a thing a lane may do: this goal says "Do not merge anything to main" and
  "Never merge", and ~139 live lane worktrees are branched off that main.
  `merge_gate.sh` therefore emits one expected WARN — it finds `449ed59` at
  `evals_repo_change.commit` and reports it is not on GitHub in
  `microsoft/amplifier-app-cli`, which is true and is the point. Gate result is
  **PASS**; the sha is in the marker deliberately, because removing it to
  silence a warning would make the other half of this item harder to find.

  What is **not** true is that the change itself is unverifiable by a third
  party. The COMMIT cannot be read back from a remote; the CHANGE can. Measured
  (`evidence/08-third-party-reconstitution.txt`): the patch fetched from GitHub
  is byte-identical to this branch's own `format-patch` (sha256
  `060a4079…f2e9b`, `cmp` identical), applies `--check` CLEAN to a fresh
  checkout of evals main `6269c44`, and in that reconstituted tree the 10 tests
  pass and this lane grades COMPLIANT at `--strict` exit 0. The commit is also
  reachable from a real branch ref, so it is not the `8bj` orphan class —
  `ORPHAN SUMMARY: CLEAN=1 ORPHAN=0 orphan_commits=0`.
* **`docs/lanes/README.md` is repo content this lane added but was not
  chartered to write.** It exists so the app-cli side of the decision is legible
  to a human who never runs the checker, and so this PR carries something other
  than a lane note. Drop it if the reviewer disagrees; nothing else depends on
  it.
