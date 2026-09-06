# Lane 6c3y — the highway watchdog dies at its own cap and nobody is told

Item: `model_performance-6c3y` · Repo: `microsoft/amplifier-app-cli` · Branch:
`lane/6c3y-watchdog-selfrestart` · Outcome: **A. RESOLVED** (all deliverables DONE;
none hit the cap).

## What was measured, before anything was changed

The item's account is right about the symptom and **incomplete about the
mechanism**. Reading the live batch's `watchdog.log` (read-only) turned up the
part that matters:

```
line     1:  2026-09-02T14:04:27Z watchdog start ... max=12h     (generation 1)
line 33456:  2026-09-03T02:10:29Z watchdog start ... max=12h     (generation 2)
line 50060:  2026-09-03T14:14:05Z WAKE: watchdog max runtime (12h) reached - restart me...
line 50274:  2026-09-06T00:26:06Z watchdog start ...             (~34h later)
```

Between 50060 and 50274 the log carries **neither `wake delivered (resume ok)`
nor `exit: deadline reached`** — the two lines the existing code writes on every
other path. So the watchdog did not exit at its cap. It was *killed*.

The transcript in between says why. The final wake **was delivered**: the resumed
manager's own reasoning is in the log — *"The watchdog has died after hitting its
12-hour max runtime … I need to … critically restart the watchdog before doing
anything else"* — and it then ran:

```
=== killing expired watchdog (12h04m) ===; tmux -L hw kill-session -t "hw-watchdog__hw-model-performance"
```

**It was running inside that session.** `wake()` calls `amplifier run --resume`
synchronously, so the responder was a child of the very watchdog it was clearing
away. Killing the session killed the responder mid-restart, before it reached the
line that starts the replacement. Hence no further log lines, and ~34h of idle.

Consequences for the fix, all three tested:

1. Option 3 in the item ("make the final message reach something that is not the
   patient") was **already implemented** — the deadline path already called
   `wake()`, which already calls `amplifier run --resume`. Delivery was never the
   problem. *Where the responder was standing* was the problem.
2. A restart that depends on someone answering a message is one participant away
   from failing. Re-arm (option 1) removes the participant.
3. `wake()` can drop the message entirely: a wake inside `WAKE_GAP` (180s)
   returns early **before** the `wake-needed` write. The one message with no next
   poll behind it must never be gappable.

## What changed

`amplifier_app_cli/data/skills/ten-lane-highway/scripts/highway_watchdog.sh`

- **`CAP RE-ARM GUARD`** — at the deadline, if the batch is open, `exec "$0" "$@"`
  with `HIGHWAY_WATCHDOG_GENERATION` bumped; the deadline resets. *Open* =
  `BATCH_DIR/lanes/` exists **AND** `.manager-heartbeat` is younger than
  `HIGHWAY_ABANDON_MAX` (default 21600s / 6h — deliberately far looser than
  `HB_MAX`'s 1800s, which describes a manager that is merely idle). A re-arm
  writes a log line and nothing else: no wake, no `wake-needed` entry.
- **`shopt -s execfail`** — a failed `exec` kills a non-interactive bash outright,
  which would have converted the re-arm into a silent stop by another name. With
  `execfail` the failure falls through to `exec "${BASH:-bash}" "$0" "$@"`. This
  was **found by the test, not by reading the manual**: the exec-bit-stripped case
  failed on first run.
- **`final_notice()`** replaces `wake()` on the wind-down path: writes
  `wake-needed` unconditionally (not gappable), then dispatches
  `amplifier run --resume` **detached** via `setsid` (falling back to `nohup`), and
  exits immediately. The prompt names the trap outright — *"The old watchdog has
  ALREADY exited: do NOT run `tmux kill-session -t hw-watchdog__<batch>` first"*.
- **`SUPERVISION HEARTBEAT`** — `.watchdog-heartbeat` touched at start and every
  poll.
- `HIGHWAY_MAX_SECONDS` — second-resolution cap override (documented escape
  hatch; also what lets the tests observe a cap boundary without waiting 12h).

`.../scripts/highway_status.sh`

- Reports `watchdog_hb_age` (JSON + `SUMMARY` line; `-1` = no heartbeat file at
  all) and, when the watchdog is DEAD, `SUPERVISION LAPSED <n>s ago`. JSON gained
  one **additive** field; existing consumers read by name and are unaffected.

`.../SKILL.md` — the cap's new behaviour, both branches, both env knobs, the
`.watchdog-heartbeat` state file, and the do-NOT-`kill-session` rule in Phase 6
step 1 where a manager will actually hit it.

`docs/lanes/6c3y-watchdog-selfrestart/check_skill_guards.6c3y.patch` — four
proposed `check` lines for the manager's `BATCH_DIR/check_skill_guards.sh`,
including the **docs half** (2nz's transferable finding: a partial re-apply is
more dangerous than a total revert). That file lives outside this repo and
outside this lane's ownership, so it is shipped as a patch, not applied.

## Deliverables

| Deliverable | State |
|---|---|
| Supervision CONTINUES past the cap while the batch is open | **DONE** — `test_cap_rearms_while_batch_is_open` asserts generation 3 from one spawned process; re-arm also proven to survive a stripped exec bit |
| Cap's original purpose preserved; a test proves BOTH branches | **DONE** — open → re-arms; no lanes dir → winds down rc=0; ancient heartbeat → winds down; widened `HIGHWAY_ABANDON_MAX` → re-arms again (proves the wind-down is the window's doing). The cap was **not** removed or raised |
| Final message reaches something that is not the patient | **DONE** — forced past `WAKE_GAP` and dispatched detached; asserted via a PATH-stubbed `amplifier` that records argv |
| Say plainly whether a manager-side inverse check is still needed | **DONE** — see below |
| Documentation ships in the same change | **DONE** — SKILL.md + drift-check patch in the same commit |
| Full suite green, pasted in the PR body | **DONE** — 1740 passed / 1 skipped / 13 deselected / 1 xfailed |
| DRAFT PR on origin | **DONE** — see `publication` in `DONE.json` |
| DONE-NOTE at the lane artifact root | **DONE** — this file |

Nothing was recorded NOT-POSSIBLE; the cap never bound (see Spend).

## Is a manager-side inverse check still needed? — **No, and I recommend against adding one**

Rejected, with the cheap half kept:

- **The expensive half is redundant.** Option 2 asks the manager to check its
  supervisor's freshness at the top of every cycle. The manager *already* runs
  `highway_status.sh` first on every wake (Phase 5 step 1, and again as the Phase 6
  turn-ending gate), and that instrument already printed `watchdog=DEAD`. A second
  manager-side check adds a second thing to remember and no new signal.
- **It would not have caught this outage**, as the item itself notes: the manager
  was idle *because* no wake arrived. Any check that runs "next cycle" is dead
  code when there is no next cycle. Re-arm needs no manager at all.
- **The cheap half is kept and is what makes acceptance criterion 3 checkable.**
  `.watchdog-heartbeat` + `watchdog_hb_age` turns "supervision lapsed" from an
  inference into a measured duration, for a stop from *any* cause — cap, crash, or
  kill — at zero cost to the manager's attention.

## Deviations and residual risk

- **Bound, honestly stated:** an orphaned watchdog can now outlive its batch by
  at most one cap period (≤12h at defaults) rather than being bounded at 12h from
  start. Abandonment is only evaluated at the cap boundary. Tightening it to
  every poll would wind down a healthy batch whose manager is legitimately quiet,
  so the looser bound is the deliberate trade. Documented in SKILL.md.
- `setsid` is util-linux; on a host without it the notice is dispatched with
  `nohup` instead. The script was already GNU/Linux-only (`stat -c %Y`).
- The item's option 3 was already present in the code. Reporting it as newly
  added would have been false; what was added is *forced* and *detached*.
- **`MAX_HOURS` was neither removed nor raised**, per the scope-out.
- Untouched, per the scope-out: `infra_ledger.sh`, `lane_teardown.sh`, every
  sweep/teardown path. `grep -c 'sweep\|teardown\|incus\|dtu'` on the two edited
  scripts: `highway_watchdog.sh` 0, `highway_status.sh` 1 — and that one is a
  pre-existing comment word (`# ended + worktree removed = normal post-teardown
  terminal state`, line 66), not code and not a line this branch changed.
- **The live watchdog was not disturbed.** Interaction with the running batch was
  read-only (`grep`/`sed` on `watchdog.log`, `ls`). Every test runs against a
  `tmp_path` batch dir on tmux socket `hw-test-6c3y`, never the default `hw`.
- Suite baseline: the goal quotes 1682 passed at `395fa68`. Re-measured on this
  host in a clean worktree at that commit: **1727 passed**, 1 skipped, 13
  deselected, 1 xfailed. With this branch: **1740 passed** — +13, exactly the new
  tests, zero regressions. The goal's figure appears stale; the +13 delta is the
  honest comparison.

## Spend

**$0.00 against an authority of $0.00** (`0 runs x 0 arms x $0 / 1.00 = $0.00`).
No API calls, no DTUs, no infrastructure created, nothing to register in the infra
ledger and nothing to tear down. The authority's arithmetic closes for a pure
shell/source change. Residue: $0.00; smallest useful purchase it could not buy:
none — this deliverable needed no purchase.
