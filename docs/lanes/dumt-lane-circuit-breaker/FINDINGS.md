# dumt — lane stop-condition + harness turn/spend circuit breaker

Work item: `model_performance-dumt`. Spend authority **$0**; **$0 spent** (text/code
edits and local test runs only, no API measurement).

---

## The incidents

Two lanes in the same batch, same shape:

| lane | turns | cost | when |
|---|---:|---:|---|
| `j1e6-ci-tool-web` | 855 | **$202.91** | after its deliverable (amplifier-module-tool-web PR #17, merged 20:19:53Z as `20c29fd`, CI green 5/5) had merged **hours earlier** |
| `hd-browser-bridge` | ~887 | **~$184** | after its PR (#14) had merged **hours earlier** |

Both repeated one evaluator message verbatim, every ~5s, with zero new tool calls:

> No verified successful `work_resolve` or `work_release` appears in the available
> transcript, so the required terminal outcome is not established.

Both goals were **unsatisfiable by construction**: the target work item was a
one-item/many-lanes container, already `resolved` and held by a sibling session, and
`work_resolve`/`work_release` both refuse a session that never held the item. No number
of further turns could change that, and none did.

---

## Where the `/goal` loop actually is

Checked, not guessed.

- **Not in this repo.** `amplifier-app-cli` only *sets* `session_state["goal"]`
  (`main.py:4253` headless, `main.py:1355` interactive) and *renders* the loop's progress
  events (`goal_progress_hook.py`). `docs/GOAL_COMMAND.md` says so, and the code agrees.
- **The loop is `StreamingOrchestrator.execute()` in
  `amplifier-module-loop-streaming`** — a different repo, outside this lane's write scope
  (read at `~/.amplifier/cache/amplifier-module-loop-streaming-b0b975ea6a1072dd/amplifier_module_loop_streaming/__init__.py`,
  goal loop at lines 1314–1590).

So the loop was instrumented at the **host** layer instead — see "What shipped" — and the
orchestrator-side fix is filed as a linked follow-up rather than guessed at.

## Why the existing stall detection could not stop it

The orchestrator already has stall detection, and it is a **dual condition**: a cheap
mechanical pre-filter, then an LLM judge that must confirm.

| layer | code | what it decides |
|---|---|---|
| trigger (a), idle | `no_tool_turns >= goal_stall_threshold` (default 3) | only whether to *pay for* a judge call |
| trigger (b), busy | `_busy_stall_pretrip` — 0.5 Jaccard overlap across a 3-reason window | only whether to *pay for* a judge call |
| the decision | `_judge_stall` (an LLM call) | whether `is_stalled` is true at all |
| the stop | `if is_stalled and (goal["escalated"] or cap_hit)` | first confirmed stall only spends an escalation turn |

Three independent ways that lets a wedged loop run forever:

1. **The judge holds the veto.** "You just have to call `work_resolve`" reads as
   *resolvable*, which is not a stall verdict — so the run continues, no matter how many
   times the pre-filter trips.
2. **It fails open.** A judge call that raises is logged and treated as "not stalled".
3. **First trip never stops anything.** It escalates; a single tool call in the
   escalation turn resets `no_tool_turns` to 0 and re-arms the whole sequence.

A byte-identical repeat is a mechanical fact. Putting a model's opinion between that fact
and the decision to stop spending is the defect.

---

## What shipped (in this repo)

### 1. Goal-template fix — `work_erratum` made load-bearing

There is no literal "lane goal template" file: lane conditions are **composed by
`goalify`**, so that is where the rule has to live to bind every lane.

- `goalify` — new **BLOCKER L7** (work-tracker terminal verb that can refuse), carrying
  the required wording for the composed condition, plus Compose #5 ("reachable terminal
  verb") for the general case (merge, deploy, publish). Version 1.2.0 → 1.3.0; both
  incidents recorded in `PROVENANCE.md`.
- `ten-lane-highway` — Phase 4 step 1 (compose time), **rule 14**, and a NEEDS-MANAGER
  disposition in Phase 5 step 6.
- `goal-batch` — Phase 3 goal-file requirement.
- `highway_status.sh` — flags a lane whose log carries a NEEDS-MANAGER report, counts it
  in the summary and the JSON line, and prints the action.

The point of L7 is that it is *not* left to each lane: this is the 4th+ lane to hit the
underlying defect, and the item's own errata trail shows at least 3 earlier ones each
independently rediscovering `work_erratum`.

### 2. Harness circuit breaker — `amplifier_app_cli/goal_circuit_breaker.py`

Judge-free, mechanical, host-side. Six consecutive **identical** (whitespace-normalized,
`(×N)`-stripped) evaluator reasons ⇒ clear `session_state["goal"]` and print a
NEEDS-MANAGER report quoting the repeated message verbatim.

- **Seam:** the orchestrator re-reads `coordinator.session_state["goal"]` at the top of
  every iteration and returns when it is falsy (`__init__.py:1315-1321`). Clearing it
  ends the pursuit with no further evaluator, stall-judge, or summary calls, and requires
  no change outside this repo.
- **Overshoot: exactly one turn**, bounded and stated — the goal is re-read at the top of
  the *next* iteration, so the turn already in flight completes. ~$0.25 against $202.91.
- **Registered on both paths** (`main.py`, interactive and headless). Headless matters
  most: nobody is watching an unattended lane's progress lines scroll past.
- Recorded at `session_state["goal_circuit_breaker"]` for the host; configurable via
  `AMPLIFIER_GOAL_REPEAT_LIMIT` (`0` disables).

**Why N = 6.** It must be strictly more conservative than the orchestrator's own
judge-backed pre-filter window of 3, because unlike that path this one is terminal and
asks no model — 6 = 2× that window leaves the full legitimate trip → escalate → re-arm →
re-trip sequence room to run first, so the judge-backed path keeps first refusal. The
costs are also wildly asymmetric: a wrong trip stops a run a manager restarts (bounded,
recoverable, and it prints exactly what it saw), a missed trip cost $202.91 and $184
(measured). That argues the threshold down, not up. Below ~4 it starts colliding with
legitimate work whose evaluator reason genuinely repeats (re-running the same failing
test while fixing it).

**Why exact-match, not fuzzy.** A judge-free terminal stop needs a near-zero
false-positive rate, and a reason differing by even one content word *is* new information
from the evaluator. Fuzzy resemblance is already `_busy_stall_pretrip`'s job at 0.5
Jaccard, backed by a judge. Two detectors, two jobs: theirs is "similar enough to ask a
model", this one is "provably identical, no model needed". Both measured incidents
repeated **verbatim**, so exact-match catches them with no false-positive surface added.

---

## Fail-before / pass-after

`tests/test_goal_circuit_breaker.py` carries `ScriptedGoalLoop`, a miniature of the
orchestrator's goal loop (re-read the goal at the top; emit `continuing` with the
evaluator reason; run the next turn), driven with the j1e6 message verbatim.

**Fail-before, captured before the module existed** (test file committed against the
pre-fix tree):

```
ERROR collecting tests/test_goal_circuit_breaker.py
E   ModuleNotFoundError: No module named 'amplifier_app_cli.goal_circuit_breaker'
1 error in 0.09s
```

**Pass-after:** `23 passed`, and the reproduction pair is permanent:

| test | asserts |
|---|---|
| `test_without_breaker_the_loop_runs_unbounded` | the same scripted loop reaches **855** agent turns — the observed j1e6 count — and the goal is still set |
| `test_with_breaker_it_self_terminates_at_the_threshold` | it stops at **≤ 7** turns (6 + the one-turn overshoot), goal cleared, trip count 6 |

Full suite: **1448 passed** before, **1471 passed** after (+23), 1 skipped, 1 xfailed.

---

## Not done here, and why

**The orchestrator-side fix is not in this repo.** The right long-term home for a
mechanical repeat breaker is `StreamingOrchestrator.execute()` itself — every host would
inherit it (amplifier-agent, amplifierd, amplifier-chat), and it could stop *before* the
overshoot turn instead of one turn after. This lane cannot write that repo, so it is
filed as a linked follow-up — **`model_performance-uz9n`**, `discovered-from
model_performance-dumt` — rather than guessed at. The host-side breaker shipped here is a real backstop, not a placeholder: the
CLI is the process that spends the money, and it needed no cross-repo coordination.
