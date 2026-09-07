# DONE-NOTE — lane `dumt-lane-circuit-breaker`

**Landing stage: DONE AT THE DRAFT PR.** Every deliverable's final state requires a merge,
and this lane may not merge. Fail-before/pass-after is demonstrated below and the work is
shipped as a **draft PR** on `amplifier-app-cli` (branch `lane/dumt-lane-circuit-breaker`).
**The merge is the manager's next stage.**

Work item: `model_performance-dumt`. Spend authority **$0**; **$0.00 spent** — text/code
edits, local test runs, and a scripted-loop reproduction only. No API measurement.

## Deliverables

| # | Deliverable | State |
|---|---|---|
| 1 | Goal-template fix: `work_erratum` named as the load-bearing terminal verb for the already-resolved-elsewhere case | **DONE** |
| 2 | Harness circuit breaker with a fail-before test reproducing an incident in miniature | **DONE** |
| 3 | Both incidents quoted verbatim in the PR body as motivating evidence | **DONE** |
| 4 | If the `/goal` loop is not in a reachable repo: say so and file a linked follow-up | **DONE** — it is not; `model_performance-uz9n` filed |

### 1 — Goal-template fix
There is no literal lane-goal template file; lane conditions are composed by **`goalify`**,
so the rule lives there to bind every lane rather than one. New BLOCKER **L7** with required
wording, plus Compose #5 (reachable terminal verb) for merge/deploy/publish; both incidents
in `PROVENANCE.md`; skill 1.2.0 → 1.3.0. Propagated to `ten-lane-highway` (Phase 4 step 1,
**rule 14**, NEEDS-MANAGER disposition in Phase 5 step 6) and `goal-batch` (Phase 3).
`highway_status.sh` now flags and counts NEEDS-MANAGER lanes.

### 2 — Circuit breaker
`amplifier_app_cli/goal_circuit_breaker.py`: judge-free, host-side. 6 consecutive identical
(whitespace-normalized) evaluator reasons ⇒ clear `session_state["goal"]` — the seam the
orchestrator re-reads at the top of every iteration — and print a NEEDS-MANAGER report
quoting the repeated message verbatim. Registered on both the interactive and headless
paths. `AMPLIFIER_GOAL_REPEAT_LIMIT` overrides N; `0` disables. Overshoot is bounded at one
turn and stated. N=6 and exact-match are argued in `FINDINGS.md` and the module docstring.

**Fail-before** (test committed against the pre-fix tree):
```
ERROR collecting tests/test_goal_circuit_breaker.py
E   ModuleNotFoundError: No module named 'amplifier_app_cli.goal_circuit_breaker'
1 error in 0.09s
```
**Pass-after:** `23 passed`. The reproduction pair is permanent: with no breaker the scripted
loop reaches **855** turns (the observed j1e6 count); with it, **≤ 7**.

**Full suite: 1448 → 1471 passed** (+23), 1 skipped, 13 deselected, 1 xfailed. Green.

### 4 — What this lane could not do
The `/goal` loop is `StreamingOrchestrator.execute()` in **amplifier-module-loop-streaming**,
not in this repo (verified by reading it; `docs/GOAL_COMMAND.md` says the same). This lane is
scoped to `amplifier-app-cli` and touched no other repo. Follow-up **`model_performance-uz9n`**
(`discovered-from model_performance-dumt`) carries the orchestrator-side version, which every
host would inherit and which would stop before the one-turn overshoot.

## What a reviewer should check

1. `docs/lanes/dumt-lane-circuit-breaker/FINDINGS.md` — why the orchestrator's existing stall
   detection structurally could not catch this (an LLM judge holds the veto, it fails open,
   and the first trip only escalates).
2. The N=6 and exact-match-not-fuzzy arguments — these are judgment calls, made explicit so
   they can be overruled with evidence rather than silently inherited.
3. Whether the host-side breaker is accepted as the right layer *for now*, given
   `model_performance-uz9n` proposes the orchestrator-side one.
