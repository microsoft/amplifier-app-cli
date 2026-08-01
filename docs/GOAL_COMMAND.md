# `/goal` — autonomous continuation

`/goal` sets a completion condition. After each turn, a separate evaluator model reads the
condition plus the conversation and answers *"is this done?"*. If not, another turn starts
automatically with the evaluator's reason as guidance. If yes, the goal clears.

Auto mode removes per-*tool* prompts. `/goal` removes per-*turn* prompts.

```
/goal <condition>                    # set a goal (unlimited turns by default) and start working
/goal --max-turns 5 <condition>      # same, with a hard turn cap
/goal --max-turns 0 <condition>      # unlimited -- explicit, same effect as the default
/goal                                # show current condition, turns, continuations, last reason
/goal clear                          # clear it (aliases: stop, off, reset, none, cancel)
```

Works in interactive mode and headless:

```bash
amplifier run --mode single "/goal all tests in tests/ pass with pytest exiting 0"
```

`/clear` also clears an active goal. Ctrl-C stops a goal run: once for graceful, twice for
immediate.

---

## Writing a condition that works

**This is the part that determines whether `/goal` saves you time or wastes your money.**
The evaluator is strict by design — it will not call an ambiguous condition satisfied. A
vague condition produces one of two failures:

- The loop keeps running against work that is already finished, burning turns on ambiguity.
- The evaluator has nothing concrete to check, and accepts a shallow claim as success.

Both are avoidable by writing the condition well.

### A good condition has three parts

| Part | Why | Example |
|---|---|---|
| **A measurable end state** | The evaluator needs something checkable | "every call site compiles" |
| **The check that proves it** | Removes ambiguity about *how* done is demonstrated | "`pytest -q` exits 0 with ≥20 tests passing" |
| **Constraints that must hold** | Negative constraints are what drift first | "no third-party web framework; stdlib only" |

Put all three in. Order doesn't matter.

### Worked example

**Weak:**
```
/goal make the API good
```
Nothing measurable, no check, no constraints. The evaluator will either loop indefinitely or
accept the first plausible-sounding claim.

**Strong:**
```
/goal Build a URL shortener REST API at ./shortener using ONLY the Python standard
library (no Flask, FastAPI, or Django). SQLite persistence. Endpoints: POST /shorten,
GET /<code> (302), GET /stats/<code>, DELETE /<code>. Duplicate alias returns 409,
invalid URL returns 400, unknown code returns 404. Done when `python3 -m pytest -q`
run from ./shortener exits 0 with at least 20 tests passing.
```

Measurable end state, an explicit check, and explicit negative constraints.

### Name the check exactly

If the condition says "tests pass," the evaluator has to guess what that means — which
command, run from where, with what interpreter. It will ask for clarification by *running
more turns*.

Write the literal command:

```
Done when `cd ./shortener && python3 -m pytest -q` exits 0.
```

> **Real example.** A run where the exact pytest command was accidentally omitted from the
> condition finished its actual work on turn 1 — 28 tests passing — but the evaluator
> correctly refused to accept an ambiguous condition and pushed back twice more before
> converging. The work was done; the wording cost two extra turns. Naming the command
> prevents this.

### State constraints as explicit MUST NOTs

Negative constraints ("maintain backward compatibility", "don't touch the migration
scripts") are the first thing to get lost over a long run. Put them in the condition, keep
them short, and make them discrete:

```
CONSTRAINT: do not modify any file outside src/. CONSTRAINT: do not add dependencies.
```

### One goal at a time

`/goal redesign auth, add OAuth, write tests, and update the docs` is four goals. The
evaluator has to judge all of them at once and will keep finding one that isn't finished.
Split compound objectives into sequential goals.

---

## Bounding the run

`/goal` is **unlimited by default** — no turn cap unless you ask for one. This is a
deliberate design decision, not an oversight: see
[`docs/decisions/ADR-0005-goal-unlimited-by-default.md`](decisions/ADR-0005-goal-unlimited-by-default.md)
for the full rationale. In short: `/goal` exists so an agent can work autonomously toward a
condition without a human babysitting every turn, and the most valuable runs are the long
unattended ones — a default cap directly undercuts that. The actual safety net is the stall
detector (below), which stops on **evidence of zero progress**, not an arbitrary turn count.

If you want a hard bound anyway — cost control, CI, an overnight run you want mechanically
fenced — set one explicitly:

```
/goal --max-turns 5 <condition>
```

This is enforced in Python, not judged by a model. When it trips:

```
──── GOAL STOPPED — turn cap hit (NOT confirmed complete) ────
  sent back to assistant: 5 continuations (cap: 5)
  last reason: ...
```

A cap hit means the run stopped **without the evaluator confirming the goal was met** — it
is not a success signal.

### Unlimited runs (`--max-turns 0`)

`--max-turns 0` explicitly requests an unlimited run — the same behavior you already get from
omitting `--max-turns` entirely. It exists for cases where being explicit in the command
(e.g. a scripted or documented invocation) is preferable to relying on the implicit default.

```
/goal --max-turns 0 <condition>
→ Goal set: <condition> (unlimited turns)
```

An invalid value fails immediately and sets no goal:

```
/goal --max-turns abc do something
→ Goal not set: Invalid --max-turns value: 'abc' -- must be a non-negative integer (0 means unlimited).
```

**Writing "or stop after 20 turns" into the condition text is not a bound** — that is a
sentence for a model to interpret. Use the flag.

### Stalled runs

Independent of the turn cap, the orchestrator can end a goal with a **`stalled`** outcome: it
detected the agent making zero progress — repeated turns with no tool calls and an
unchanging blocker — and gave up rather than burning turns against nothing:

```
──── GOAL FAILED — stalled (no progress detected) ────
  sent back to assistant: 3 continuations
  last reason: ...
  stalled on: agent repeated the same blocked claim 3 turns in a row without making a tool call
  reason history:
    - ...
```

`stalled` is a **failure outcome**, same as hitting an unhandled error — the goal was not
achieved. It is not gated by `--max-turns`; it can trip well before the cap if the run is
genuinely stuck.

### What the cap does NOT bound

`--max-turns` bounds **goal continuations** — the number of times the goal loop starts a new
turn. It does **not** bound work happening *inside* a single turn.

An agent can make an unlimited number of tool calls within one turn; the turn ends only when
the model stops requesting tools. If an agent gets stuck polling something that never
completes, it never ends its turn, the goal evaluator never runs, and **the cap never fires.**

This was verified: an agent given a job-status script that always exited 0 and never reached
100% polled it 59+ times inside a single turn with `--max-turns 8` set. The cap was never
reached because no turn ever ended.

`/goal` is a **completion gate**, not a runaway guard. It catches an agent that stops too
early. It cannot catch one that never stops. For unattended runs, bound the process
externally (a wall-clock `timeout`, a CI job limit) in addition to `--max-turns`.

---

## What the evaluator can and cannot see

The evaluator reads **the conversation**. It has **no tools** — it does not run commands or
read files independently.

This has one important consequence: **work that is never surfaced into the conversation is
invisible to it.** If the agent runs the test suite but never prints the result, the
evaluator cannot confirm it passed. Conditions that ask the agent to *show* its verification
("you must actually run the tests and show the real output") work better than conditions
that only describe the end state.

It also means the evaluator can be satisfied by a *claim* rather than a fact. `/goal` is not
a substitute for verifying important work yourself.

---

## Status and progress

`/goal` with no arguments shows the condition, turns evaluated (against the cap if set), how
many times the goal has been sent back to the assistant for another turn (continuations),
and the evaluator's most recent reason. If there's more than one reason recorded, the last
few are shown too, so you can see whether the evaluator is repeating itself:

```
Goal: <condition>
Turns evaluated: 4/20
Continuations (sent back to assistant): 3
Last evaluator reason: ...
Recent reasons:
  - ...
  - ...
  - ...
```

The reason (and reason history) is the entire progress signal. There is deliberately no
percentage, no milestone list, and no burn-down — a synthesized progress number would be a
guess presented as a measurement.

At the end of a run (achieved, cap hit, cancelled, error, or stalled), the CLI prints a
distinct end-of-run block with the outcome, the continuation count, and — when available — a
fast-model summary of the run. `cap_hit` and `stalled` are both rendered as **not
successful** (the goal was not confirmed complete); only `achieved` is a success outcome.

---

## Cost

Each turn adds one evaluator call on the small/fast model, which is minor next to the main
turn. The real cost driver is the number of turns, which is driven by how well the condition
is written. A well-specified condition converges; a vague one does not.

`/goal` is unlimited by default (see
[ADR-0005](decisions/ADR-0005-goal-unlimited-by-default.md)); use `--max-turns N` if you want a
mechanical bound for CI, cost control, or an unattended overnight run.
