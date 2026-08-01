# ADR-0005: `/goal` Is Unlimited by Default (No Turn Cap)

**Status**: Accepted
**Date**: 2026-08-01
**Context**: Stall-detection work for `/goal`; reverts an in-flight default-20-turns decision

---

## Decision

`/goal` has **no turn cap by default**. Omitting `--max-turns` means unlimited continuation
until the evaluator confirms the condition is satisfied, the run stalls (see below), or the
user cancels (Ctrl-C). `--max-turns N` remains available to set a hard, Python-enforced cap;
`--max-turns 0` is an explicit (and equivalent) way to ask for unlimited.

This is a considered, deliberate decision. A prior iteration of this work changed the default
to 20 turns; that change is reverted by this ADR, and the reasoning below is why it should not
be reintroduced.

---

## Context

While building stall detection for `/goal`, an in-progress change set the default turn cap to
20 (previously: unlimited), on the reasoning that unbounded auto-continue loops can burn hours
and cost before anyone notices. That reasoning is valid on its own terms, but it optimizes for
the wrong failure mode and trades away the feature's actual purpose. This ADR documents why
the repo owner overruled it and restored unlimited-by-default.

## Why unlimited is correct

**`/goal` exists to remove the human from the loop, not to babysit it in 20-turn increments.**
The entire value proposition of `/goal` is that an agent can work autonomously toward a
condition without a person reviewing and re-prompting every turn. The most valuable runs are
exactly the long, unattended ones -- a substantial refactor, a full test-suite fix, a build
that takes real iteration to converge. A default cap that trips at 20 turns directly undercuts
that purpose: it forces exactly the kind of check-in-and-relaunch behavior `/goal` was built to
eliminate, on every run long enough to matter.

**A turn count is a poor proxy for safety.** It cannot distinguish an agent making steady,
productive progress from one that is spinning uselessly. A fixed cap punishes productive long
runs (stopping a real, convergent effort at turn 20 for no reason but the count) and permits
unproductive short runs equally (an agent can burn 19 turns going in circles and the cap never
notices, because it isn't watching for circles -- it's watching a clock). Turn count is
orthogonal to the thing we actually care about, which is whether progress is happening.

**The real safety mechanism is the stall detector, not a count.** This same work built a stall
detector that watches for **evidence of zero progress**: consecutive continuation turns with no
tool calls, corroborated by a fast-model judge confirming the blocker is unchanged turn over
turn. That is a real signal grounded in what the agent is actually doing, not an arbitrary
number picked because *some* bound felt safer than none. Once that mechanism exists, a turn cap
is no longer standing in for a missing safety net -- it would only be redundant, and a
redundant cap still carries the downside above (punishing valid long runs) for no offsetting
benefit.

**`--max-turns N` is still there for anyone who wants a bound.** Cost control, CI pipelines,
and unattended overnight runs are legitimate reasons to want a hard mechanical fence. The flag
supports that fully; it is simply not forced onto every invocation by default.

## What this ADR does NOT change

- `--max-turns N` (N > 0): still a hard, Python-enforced cap, unchanged.
- `--max-turns 0`: still an explicit, equivalent way to request unlimited (harmless, and
  useful for scripted/documented invocations that prefer to be explicit).
- Malformed `--max-turns` values: still fail loud (`ValueError`), never silently fall back to
  any default.
- The stall detector, reason history, continuation counting, and terminal-state rendering
  built alongside this decision are all unaffected -- only the *default cap value* is reverted.

## Consequences

- An `/goal` run with no `--max-turns` flag can, in principle, run indefinitely if the
  evaluator never confirms completion and the stall detector never trips. This is accepted:
  it is what "unlimited by default" means, and it mirrors the behavior of comparable
  agentic auto-continue tools that also default to no bound.
- Because the default is now the common case, the CLI does **not** print a loud warning on
  every unlimited run -- that would be noise on the normal path and would train users to
  ignore warnings. The confirmation line (`Goal set (unlimited turns).`) states the fact
  plainly without alarm, and without echoing the condition text the user just typed.
- Anyone wanting a mechanical fence must opt in with `--max-turns N`. This is documented in
  `docs/GOAL_COMMAND.md`.

## Future contributors: do not re-add a default cap

If you find yourself reaching for "let's cap `/goal` at N turns by default to be safe" --
don't. That tradeoff was made once, reasoned through, and reverted (this ADR). The safety
mechanism for runaway/no-progress runs is the stall detector, not a count. If the stall
detector proves insufficient in practice, that is a reason to improve the stall detector, not
to reintroduce an arbitrary turn cap as a substitute.

## Related

- `docs/GOAL_COMMAND.md` -- user-facing documentation for `/goal`, including bounding the run
  and stalled-run behavior.
- `amplifier_app_cli/main.py` -- `_parse_goal_max_turns`, `_GOAL_MAX_TURNS_FLAG`.
- `amplifier_app_cli/goal_progress_hook.py` -- terminal-state rendering, including `stalled`.
