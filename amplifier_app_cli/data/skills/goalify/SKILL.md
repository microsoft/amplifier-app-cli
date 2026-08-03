---
name: goalify
description: >
  Compose a /goal stop-condition from the current conversation and lint it
  against known termination-failure patterns before showing it to the user.
  Use when the user wants to turn the current task into a /goal loop, asks to
  "goalify this", wants a stop condition for autonomous work, says "write a
  goal condition", "make this a /goal", "turn this into a goal", or asks for
  help wording a condition for /goal.
user-invocable: true
version: 1.1.0
license: MIT
---

Run this procedure yourself, in the current conversation. Do not delegate it
to a sub-agent or forked session — Phase 1 reads the live transcript.

$ARGUMENTS

If the user supplied focus text above, use it to scope Phase 1. If empty,
extract from conversation alone — do not ask the user to restate what they
already said.

---

## Phase 1 — Extract

From the conversation so far, determine:

- **The target end state.** What does "finished" concretely look like? It
  must be a state that can be checked, not an activity that can be performed
  indefinitely. ("a usable app" is checkable; "finish building the project"
  is not — there is no test for "finished building".)
- **What is already done.** Re-read the transcript for completed sub-tasks,
  passing tests, merged changes, or resolved questions. These become
  candidates for the KNOWN section (Phase 2) or for narrowing scope
  (SCOPE-OUTS).
- **What is explicitly NOT required.** Anything the user has ruled out,
  deferred, or said isn't needed. This is the raw material for SCOPE-OUTS.

If the end state genuinely cannot be determined from context (not merely
effortful to determine), ask one direct question. Otherwise proceed —
guessing and then showing your extraction for correction is faster than
front-loading a question the transcript already answers.

## Phase 2 — Compose

Emit a candidate condition using this structure. Every element is required
unless marked optional.

1. **One-sentence outcome** naming a checkable end state (not an activity).
2. **Disjunctive exit**: the condition must be satisfiable by *either*
   reaching the end state *or* conclusively demonstrating it cannot be
   reached (naming the blocker). Never phrase a condition with only one exit.
3. **Per-item negative terminal**, if the condition lists multiple items
   (tasks, sites, phases, experiments). Each item must be able to resolve to
   its own PASS / FAIL / BLOCKED-with-named-reason — a blocker on one item
   converts *that item* to a residual; it must not block the whole goal.
4. **SCOPE-OUTS section** — an explicit list of what is *not* required. Write
   this by directly converting anything from Phase 1's "not required" list
   into a plain negative statement (e.g. "No production soak time required."
   / "Uniformity across all N items is NOT the goal.").
5. **KNOWN section (optional)** — facts already established, so the actor
   doesn't re-derive them. Label it explicitly as a speed aid: it prevents
   wasted turns, it does not by itself prevent stalls, so it never replaces
   items 1–4.

## Phase 3 — Lint

Check the full composed document against every rule below. Work through all
BLOCKERS first; a document with any BLOCKER triggered is not ready to show.
Then check WARNINGS, which are advisory and do not block presentation.

**Read the whole document for each check.** Several of these rules are only
detectable by considering the document as one system — a single clause
elsewhere can silently defeat a correct-looking rule everywhere else. Do not
scan for keywords in isolation and stop at the first clean-looking match.

### BLOCKERS — fix all before presenting

- **L1 — Ordering/provenance constraint on the transcript's own history.**
  Any phrasing that requires evidence to precede, or be produced independent
  of, events that already exist in the transcript (e.g. "verify it yourself,
  then state what you verified", "proof must precede the claim", "evidence
  you produced yourself" applied to something already reported by a
  sub-agent or prior turn). This class of requirement is **unrepairable** —
  no later turn can change what already happened earlier in the transcript,
  so it cannot be fixed by adding more work. If the condition constrains
  ordering, it must constrain only *future* actions, never re-litigate what
  is already in the history.

- **L2 — Universal quantifier over a set with possibly-exempt members.**
  "all N", "every X", "each of the Y", "uniform/uniformity", "complete
  parity", applied to a set, is a blocker **unless** each item individually
  carries a negative terminal (see Compose #3) or the condition names which
  members are exempt and why. Without one of those, a single member that
  cannot structurally produce the required evidence makes the whole
  condition permanently unsatisfiable.

- **L3 — Elapsed wall-clock requirement.** Anything that requires real time
  to pass beyond the current session: "production soak", "after N days of
  use", "monitor over time", "verify in real-world use". A single session
  cannot advance wall-clock time; this can never be satisfied in-session.

- **L4 — Human-in-the-loop or external-actor dependency mid-loop.**
  "stop and ask me if you need a decision", "once a reviewer merges this",
  "wait for approval before continuing". This directly conflicts with
  unattended continuation — the loop will halt waiting on an event that a
  condition-checking loop cannot itself produce.

- **L5 — Open enumeration.** Scope phrased as an unbounded or unenumerated
  set: "all editing features of X", "complete parity with Y", "everything
  needed to fully support Z". An evaluator can always name one more item
  under this phrasing, so it never terminates. Convert to a closed, named
  list, or to a single representative artifact.

- **L0 — Cross-clause consistency (meta-rule).** *An escape hatch is only as
  strong as the strictest other clause in the same document.* After
  confirming L1–L5 pass individually and a disjunctive exit exists, re-read
  the document once more asking only: **is there any other sentence, anywhere
  in the document, that is stricter than the stated exit and would override
  it?** A document can have a textbook-perfect exit clause and still be
  unsatisfiable because one unrelated sentence elsewhere re-imposes an L1–L5
  style constraint the exit clause doesn't cover. Confirming an exit clause
  exists is not sufficient — confirm nothing else in the document is
  stricter than it.

### WARNINGS — advisory, do not block presentation

- **L6 — Missing disjunctive exit.** The document should state achievement
  *or* a way to conclusively end in "not achievable, here is why" (see
  Compose #2). Flag and fix its absence where practical, but do not block
  presentation on it alone.
- **W1** — Multiple items are listed but not all of them carry their own
  negative terminal (some do, some don't).
- **W2** — No clause asking the actor to show evidence inline in the
  transcript as it's produced, rather than only asserting a result.
- **W3** — Scope reads like more than one session's worth of work (multi-week
  rollout language, coordination across many independent repos/teams,
  phased production deployment).
- **W4** — The condition contains a cautionary anecdote or narrative about a
  failure mode (e.g. "don't repeat what went wrong last time", "make sure
  this doesn't stall like before") rather than a plain instruction. Any such
  narrative addressed to the actor is read by the evaluator too, and can
  silently become a criterion the evaluator judges against instead of
  guidance the actor merely follows. State requirements as plain criteria,
  never as stories.

  **This applies to the condition you are composing right now.** Write every
  clause as a direct instruction to the actor, never as a story about a past
  run. If you catch yourself writing "so that we don't repeat X", rewrite it
  as the direct requirement it implies, with no reference to the incident.

### If a BLOCKER cannot be cleared

Rewrite and re-check. Allow up to three rewrite passes. If a BLOCKER still
fires after three passes, stop and surface the specific tension to the user
by name (e.g. "the user's own request requires enumerating an open-ended set
— L5 fires no matter how I phrase it; how would you like to bound this?").
Do not present a condition that still fails a BLOCKER.

---

## Output format

Always output the condition inside a fenced code block — terminal reflow
will otherwise destroy its multi-line structure. Follow it with the lint
report as a table, then offer (do not auto-run) `/goal`.

```
<the condition text>
```

| Rule | Result | Note |
|------|--------|------|
| L0 | no known pattern detected | ... |
| L1 | no known pattern detected | ... |
| L2 | no known pattern detected | ... |
| L3 | no known pattern detected | ... |
| L4 | no known pattern detected | ... |
| L5 | no known pattern detected | ... |
| L6, W1–W4 | (list only the ones that fired) | ... |

A clean table means no *known* failure pattern was detected — not that the
condition is validated. Say so if the user reads it as a guarantee.

Then: "Pass this to `/goal` to start the loop — want me to run it now, or
would you like to adjust anything first?"
