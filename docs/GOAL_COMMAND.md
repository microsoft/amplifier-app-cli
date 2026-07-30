# `/goal` — autonomous continuation

`/goal` sets a completion condition. After each turn, a separate evaluator model reads the
condition plus the conversation and answers *"is this done?"*. If not, another turn starts
automatically with the evaluator's reason as guidance. If yes, the goal clears.

Auto mode removes per-*tool* prompts. `/goal` removes per-*turn* prompts.

```
/goal <condition>                    # set a goal and start working
/goal --max-turns 20 <condition>     # same, with a hard turn cap
/goal                                # show current condition, turns used, last reason
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

By default there is **no turn cap** — the loop runs until the evaluator is satisfied. For
unattended or CI runs, set a mechanical bound:

```
/goal --max-turns 20 <condition>
```

This is enforced in Python, not judged by a model. When it trips:

```
⚠ goal: hit turn cap (20) — stopping.
```

An invalid value fails immediately and sets no goal:

```
/goal --max-turns abc do something
→ Goal not set: Invalid --max-turns value: 'abc' -- must be a positive integer.
```

**Writing "or stop after 20 turns" into the condition text is not a bound** — that is a
sentence for a model to interpret. Use the flag.

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

`/goal` with no arguments shows the condition, turns used (against the cap if set), and the
evaluator's most recent reason.

That reason is the entire progress signal. There is deliberately no percentage, no milestone
list, and no burn-down — a synthesized progress number would be a guess presented as a
measurement.

---

## Cost

Each turn adds one evaluator call on the small/fast model, which is minor next to the main
turn. The real cost driver is the number of turns, which is driven by how well the condition
is written. A well-specified condition converges; a vague one does not.

Use `--max-turns` for anything unattended.
