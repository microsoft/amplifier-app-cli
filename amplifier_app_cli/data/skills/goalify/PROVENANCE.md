# goalify — rule provenance

Not loaded by `load_skill`. This file exists so the lint rules in `SKILL.md`
can be audited and re-derived. Read it when changing a rule, not when using
the skill.

## Where the rules come from

Every BLOCKER traces to an observed `/goal` run that failed to terminate.
That property is the ruleset's credibility. **Do not add a rule without a
named run or a corpus measurement behind it.**

| Rule | Origin |
|---|---|
| L1 — ordering/provenance constraint | A run whose condition required `PROVEN with evidence you produced yourself`. The evaluator crystallised this into a constraint on the transcript's own past and stated it could not be retroactively repaired. Stalled after 54 turns; unreachable from roughly turn 45. |
| L2 — universal quantifier with exempt members | A run requiring evidence for `all 9 sites` in a uniform structure, where one site could not structurally produce that evidence. Stalled after 24 turns. |
| L3 — elapsed wall-clock requirement | A run whose only available proof standard was real-world use over time. Stalled after 31 turns. |
| L4 — human-in-the-loop dependency | A condition containing `Stop and ask me if you need a decision from me`, which contradicts unattended continuation. 12 turns consumed in 35 seconds. |
| L5 — open enumeration | `all editing features of Publisher` — never terminated. Its sibling run in the same session, same codebase, same day (`until I have a usable app`) achieved on turn 1. |
| L0 — cross-clause consistency | The L2 run above carried a textbook four-verdict exit vocabulary and stalled anyway, because one other sentence silently overrode it. |
| L6 — missing disjunctive exit | Advisory, not blocking. See below. |

## Why L6 is a WARNING, not a BLOCKER

A lint regression over 30 scored real runs with known outcomes measured L6
firing on 37% of all real conditions at a 9% hit rate — the largest single
source of false positives of any rule, consistent across the tuning split,
the held-back split, and the full set. L6 judges exit-clause presence in
isolation and cannot see turn position or residual scope, so it routinely
flagged short finisher-style conditions written late in a long session.

On that corpus, presence of a disjunctive exit was only weakly correlated
with actual termination — several conditions with strong exit language
stalled anyway, for reasons L1/L2/L0 cover independently.

Demoting L6 raised measured precision from 20% to 43%, recall unchanged
at 100%. L0 and L1–L5 each fired only 1–2 times in that evaluation — too few
observations to justify changing their classification, so none was changed.

## How to read the precision number

The 20%/43% figures were measured on a corpus dominated by **human-authored**
conditions, which terminate about 96% of the time. On a population that
rarely fails, any linter's precision is bounded by arithmetic — a rule that
flags a condition which succeeded anyway counts as a false positive.

This skill lints **agent-authored** conditions, which in the same corpus
terminated about 60% of the time. Same rules, roughly ten times the base
rate of true positives. **Treat 43% as a floor measured on the easiest
available population, not as the operating precision.** Re-scoring against
the agent-authored subset alone is the outstanding measurement.

The asymmetry also justifies the operating point: a false positive costs one
in-session rewrite pass; a false negative costs a 24-to-54-turn unrepairable
stall. High recall at moderate precision is the correct trade here.

## Standing caveats

- **The corpus ages.** These rules encode `/goal` evaluator semantics as
  observed at the time of measurement. If the goal loop's evaluator changes,
  nothing will automatically flag that the rules have rotted.
- **Effective sample size is smaller than it looks.** The scored runs came
  from roughly 8 distinct sessions; runs within a session share an author, a
  project, and phrasing habits.
- **The skill drafts; the human edits.** Automating goal authoring is itself
  the thing that raises failure rates (60% vs 96%). The lint is the bet that
  it closes that gap, and that bet has not been measured end to end. The
  human review step is load-bearing, which is why the skill offers and never
  auto-runs.
