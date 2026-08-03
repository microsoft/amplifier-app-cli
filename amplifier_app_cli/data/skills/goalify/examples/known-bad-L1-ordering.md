# Known-bad example — L1 (ordering/provenance constraint)

Used to sanity-check the lint in `SKILL.md` Phase 3. This condition is
modeled on a real run that stalled for many turns because the ordering
constraint it imposes on the transcript's own history became impossible to
satisfy once evidence had already been reported by a sub-agent.

## Condition text

```
Done when the migration is PROVEN complete, with evidence you produced
yourself. A sub-agent's report is not proof. Verify it yourself, then state
what you verified — or show a named blocker and stop.
```

## Expected lint result

- **L1: FAIL** — "with evidence you produced yourself" combined with "verify
  it yourself, then state what you verified" imposes an ordering constraint
  on the transcript's own history. If a sub-agent already reported a result
  earlier in the transcript, no later turn can retroactively make that
  report "verified by you first" — the requirement is unrepairable once the
  transcript already contains the sub-agent's report.
- L6 present (disjunctive exit exists: "or show a named blocker and stop"),
  but L1 alone should be sufficient to fail this condition.
