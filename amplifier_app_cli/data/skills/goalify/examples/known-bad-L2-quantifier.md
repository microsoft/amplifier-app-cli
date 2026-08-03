# Known-bad example — L2 / L0 (universal quantifier + cross-clause override)

Used to sanity-check the lint in `SKILL.md` Phase 3. Modeled on a real run
that stalled because a uniform requirement applied to every member of a set
made the condition permanently unsatisfiable once one member could not
structurally produce the required evidence.

## Condition text

```
Done when all 9 sites are migrated in a uniform structure, verified working,
or proven impossible with a named blocker.
```

## Expected lint result

- **L2: FAIL** — "all 9 sites" + "uniform structure" is a universal
  quantifier over a set, with no per-item negative terminal and no named
  exemption for a site that cannot physically satisfy "uniform structure."
- **L0: FAIL** — the document has a disjunctive-looking exit ("or proven
  impossible with a named blocker"), but that exit applies to the *goal as a
  whole*, not per-site. The stricter clause ("uniform structure" across all
  9) is not covered by the escape hatch, because the escape hatch only
  fires once — it can't let 8 sites succeed uniformly while 1 is blocked and
  still call the set "uniform." Confirming the exit clause exists is not
  enough; it does not cover the stricter clause.
- L6 nominally present (a disjunctive-shaped phrase exists) but does not
  actually rescue the condition — this is exactly the L0 case: an escape
  hatch that reads as satisfied but is overridden by a stricter clause
  elsewhere in the same document.
