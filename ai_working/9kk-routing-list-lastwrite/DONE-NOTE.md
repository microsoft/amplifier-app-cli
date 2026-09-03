# DONE-NOTE — `model_performance-9kk`

`amplifier routing list` picked the winning matrix row by last-write-wins, not by the
loader's rule. It could therefore name a file as in use that the loader would never read.

**Spend: $0.00.** No API calls, no DTU, no infrastructure created or registered.
Everything here is code reading, unit tests, and one local CLI invocation.

**Stacked on adq's open PR #293** (`lane/adq-routing-list-shadowing`), as instructed —
#293 was still open at the time of writing. Base retargets to `main` once #293 merges.

---

## Deliverable 1 — the last-write-wins resolution, quoted, and why it diverges

**`amplifier_app_cli/commands/routing.py:153-160`** (as of `origin/main` @ `31ad917`;
`154-170` after #293 renamed it to `_load_all_matrices_with_paths`):

```python
def _load_all_matrices(matrix_files: list[Path]) -> dict[str, dict[str, Any]]:
    """Load all matrix files into a name -> data dict."""
    matrices: dict[str, dict[str, Any]] = {}
    for path in matrix_files:
        data = _load_matrix(path)
        if data and "name" in data:
            matrices[data["name"]] = data      # <-- last write wins
    return matrices
```

fed by **`routing.py:141`**:

```python
    return sorted(files)                        # <-- bundle dirs, then custom dir
```

Two independent divergences from the loader, in one line:

| | the CLI did | hooks-routing does |
|---|---|---|
| **key** | the `name:` field *inside* the YAML | the **file stem** — `search_dir / f"{default_matrix_name}.yaml"` |
| **winner** | whichever file comes **last** in `sorted()` | the **first** hit in `[*custom_routing_dirs, routing_dir]` |

The loader's rule, quoted from the shipped bundle
(`amplifier_module_hooks_routing/__init__.py:88-96`, the pre-#52 inline form):

```python
    search_dirs = [*custom_routing_dirs, routing_dir]
    matrix_path = next(
        (
            candidate
            for search_dir in search_dirs
            if (candidate := search_dir / f"{default_matrix_name}.yaml").exists()
        ),
        None,
    )
```

and its post-#52 form (`routing-matrix` @ `320f24e`,
`__init__.py:108-111` → `matrix_loader.py:73-144`):

```python
    matrix_origin = resolve_matrix_source(
        default_matrix_name, custom_routing_dirs, routing_dir
    )
    matrix_path = matrix_origin.path
```

**Why they agreed until now, by accident:** `sorted()` puts
`~/.amplifier/cache/…` before `~/.amplifier/routing/…` only because `"c" < "r"`.
The user file therefore landed last and won under both rules. Nothing enforced that;
renaming either directory silently flips the CLI's answer with no error.

**The two ways it broke, both now covered by tests:**

1. **`name:` ≠ stem.** A user file `my-fast.yaml` declaring `name: balanced` sorted last
   and *overwrote the row for the real `balanced` matrix*. `routing list` and
   `routing show balanced` then displayed a file the loader can never resolve as
   `balanced` — the tool actively asserting something false. And `routing use` wrote that
   internal name into settings, which the loader appends `.yaml` to → "Matrix file not
   found — routing disabled".
2. **Sort order flips.** Move the bundle cache to any path sorting after
   `~/.amplifier/routing/` and last-write-wins hands back the *bundle* file while the
   loader loads the *user* file.

---

## Deliverable 2 — the fix (DRAFT PR, branch `lane/9kk-routing-list-lastwrite`)

Rows are now keyed by **file stem**, and the file behind each row is the one
`resolve_matrix_source()` would load.

`resolve_matrix_origins()` (shipped by #293) already reaches hooks-routing's own
`resolve_matrix_source` by loading `matrix_loader.py` out of the cached bundle, and its
`MatrixSource.path` is literally the value the loader assigns to `matrix_path`. So the
fields ell published **are** reachable from the CLI — via #293's seam — and this change
consumes them rather than re-deriving precedence a third time. No new seam was needed.

- `lib/routing_provenance.py` — new `resolve_winning_paths(matrix_files, origins)`:
  `{stem: winning_path}`, taken verbatim from `MatrixSource.path` when available.
- `commands/routing.py` — `_load_all_matrices_with_paths()` now selects the winner first
  and parses **only** that file, so a shadowed file can no longer supply a row's
  description, `updated:` date, or compatibility count.
- `_load_all_matrices()` is keyed by stem, which also fixes `routing use` writing a value
  the loader cannot resolve.
- A `name:`/stem disagreement is surfaced (row marker, footer note, `declared_name` in
  JSON) instead of being silently keyed on the internal name.
- Every row's JSON now carries `matrix_file` — which file this row is.
- `routing use <internal-name>` is refused *and* names the filename to use instead.

### Decision recorded: what happens when `resolve_matrix_source` is unreachable

A cached bundle older than routing-matrix PR #52 has no function to ask. **This is the
live state on the measurement host** — `~/.amplifier/cache/amplifier-bundle-routing-matrix-972b0ce7f0cbc2f7`
carries no `resolve_matrix_source`, so `resolve_matrix_origins()` returns `{}` there today.

#293's rule is "a wrong shadowing marker is worse than none", and that is kept — no marker
is drawn. But row *selection* is not symmetric with a marker: a marker may be omitted,
because "no claim" is truthful; a listing row cannot be omitted, so something must be
chosen. Choosing by alphabetical accident is what this item is about.

**Chosen:** fall back to the first candidate in `[*custom_dirs, *bundle_dirs]` — the same
list hooks-routing builds as `search_dirs`, using #293's existing `classify_routing_dirs()`.
It lives in one function, is labelled as a fallback in its docstring, and is only reached
when the authoritative answer is unavailable. Recorded here rather than escalated, per the
lane's no-waiting rule.

---

## Deliverable 3 — the disagreement test

`tests/test_routing_winner_selection.py` (17 tests). The two rules are made to
**disagree explicitly**, and each disagreement class carries a non-vacuity gate that
re-runs the old algorithm inline (`_last_write_wins()`) and asserts it picks the *other*
file — so the tests cannot quietly stop testing anything if the trees stop colliding.

| construction | last-write-wins picks | loader picks | test |
|---|---|---|---|
| bundle under `.amplifier/zz-cache/…` so it sorts **after** `.amplifier/routing/` | bundle file | **user file** | `TestSortOrderDisagreement` (5) |
| user `my-fast.yaml` declaring `name: balanced` | `my-fast.yaml`, keyed `balanced`, real `balanced` row gone | **bundle `balanced.yaml`**, plus a separate `my-fast` row | `TestNameStemDisagreement` (6) |

Also pinned:

- **provenance unreachable** — fallback still picks the user file, still draws no
  shadowing marker (`TestProvenanceUnreachableFallback`, 2);
- **agreeing tree unchanged**, **no user routing dir at all**, **stock shadowed layout
  still picks the user file**, and an **unparseable winner drops the row** rather than
  letting the shadowed loser stand in (`TestUnchangedBehaviour`, 4).

### Honest limitations

- `resolve_matrix_source` is exercised through a **stand-in** reproducing the upstream
  contract (`routing-matrix` `d17d03c` / verified against `320f24e`), because
  hooks-routing is a bundle module with nothing to import in a test environment. This
  mirrors #293's own approach; the CLI's real consumption path (locate bundle → load
  module by file path → call → render) is exercised end to end.
- The stand-in string is duplicated between `test_routing_shadowing.py` and
  `test_routing_winner_selection.py`. Deliberate: de-duplicating it means editing
  #293's test file while #293 is open.
- No session was actually started against a disagreeing tree — the claim "the loader
  would load X" rests on quoting `resolve_matrix_source` and on #293's dynamic load of
  the real function, not on a booted session. **(confidence: measured for the CLI's own
  selection; inferred for the loader's end behaviour.)**

---

## Verification

- `uv run pytest -q` → **1605 passed, 1 skipped, 13 deselected, 1 xfailed**.
- Existing `routing list`/`show`/`use` tests (`test_routing_commands.py`,
  `test_routing_shadowing.py`, `test_routing_matrix_registration.py`) → 101 passed,
  unmodified.
- `ruff check` clean on all three touched files; the repo's 14 pre-existing findings
  are unchanged (verified by stashing).
- **Live smoke on the measurement host** (12 matrices, 6 user files, pre-#52 bundle):
  console output byte-identical to the pre-change branch, and `--format json` now names
  the winner per row — `anthropic` → `~/.amplifier/routing/anthropic.yaml`,
  `openai` → `~/.amplifier/routing/openai.yaml`, the other ten → the bundle cache. Both
  shadowed matrices resolve to the user file, which is what a session loads.

## What remains open

- Once a post-#52 routing-matrix bundle is cached, the shadowing markers this host cannot
  currently draw will appear for `anthropic` and `openai`. Worth re-running the smoke check
  then — it is the first host state where the authoritative path, not the fallback, is live.
- `_show_matrix_details()` still titles the panel from `matrix_data["name"]`, so a
  `name:`/stem mismatch shows the internal name in that one header. The disagreement is
  reported alongside it; unifying the header was left out of scope.
