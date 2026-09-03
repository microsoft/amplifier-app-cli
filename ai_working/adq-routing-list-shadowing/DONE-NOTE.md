# DONE-NOTE — `model_performance-adq`

`amplifier routing list` does not mark a user matrix as shadowing a bundle matrix
(app-cli, cross-repo). Lane dir: `lanes/adq-routing-list-shadowing/`.
Worktree: `amplifier-app-cli` @ branch `lane/adq-routing-list-shadowing`.

**Spend: $0.00.** No API calls beyond this session's own reasoning, no DTU, no
containers, no infrastructure registered or created. Nothing to tear down.
(Two scratch dirs under `/tmp` — `/tmp/adq-demo-home`, `/tmp/rm-upstream` — were
removed at the end; they are not infrastructure and were never registered.)

---

## Deliverables

| # | deliverable | status |
|---|---|---|
| 1 | routing list implementation named at file:line + what it displays | **DONE** |
| 2 | DRAFT PR on origin, `lane/adq-routing-list-shadowing`, tests green | **DONE** — [microsoft/amplifier-app-cli#293](https://github.com/microsoft/amplifier-app-cli/pull/293) |
| 3 | a test for the shadowed case AND a test proving unshadowed output unchanged | **DONE** |
| 4 | a before/after sample of the new output in the PR body | **DONE** |

---

## (1) The implementation, at file:line

*(confidence: measured · evidence: files read in this worktree at `0d93352`)*

| what | where |
|---|---|
| the `list` command | `amplifier_app_cli/commands/routing.py:297-354` |
| where the files come from | `_discover_matrix_files()` — same file, `:98-141` |
| where they become rows | `_load_all_matrices()` — same file, `:153-160` |

**What it displayed before this change.** `_discover_matrix_files()` globs
`~/.amplifier/cache/amplifier-bundle-routing-matrix-*/routing/*.yaml` (lazily
fetching the bundle on a clean install) plus `get_custom_routing_dir()` →
`~/.amplifier/routing/*.yaml`, and returns `sorted(files)` — a flat list with
no record of which directory each file came from. `_load_all_matrices()`
collapses that list into a `name -> data` dict keyed by the `name:` field
*inside* each YAML, last write wins. `routing_list` then renders one row per
dict entry: active arrow, name, `description`, `covered/total roles`
compatibility, `updated`.

**Consequence:** two same-named files produce exactly **one** row. The reader
cannot see that a collision happened, which file won, or that the other one is
dead. That is the defect.

---

## (2) Reachability of ell's published fields — the finding that shaped the design

*(confidence: measured · evidence: import attempt + upstream clone at `d17d03c`)*

The item asked to consume ell's fields rather than re-implement discovery, and
to say so precisely if they are not reachable. **They are not reachable from
this process.** Two independent reasons:

1. **`resolve_matrix_source` is not importable.**
   `uv run python -c "import amplifier_module_hooks_routing"` →
   `ModuleNotFoundError`. `hooks-routing` is a *bundle module*, not a
   distribution `amplifier-app-cli` depends on (see `pyproject.toml`
   `dependencies`), and it is not on `sys.path`.
2. **The capability fields are not readable either.** `matrix_path` /
   `matrix_source` / `shadowed_paths` are published on the
   `model_role_resolver` capability **at session start**. `amplifier routing
   list` never mounts a bundle — there is no coordinator and no capability in
   that process at all.

**Smallest seam that still avoids re-implementing precedence (what shipped):**
the routing-matrix bundle *is on disk*, in the same cache directory
`_discover_matrix_files()` already globs. So the CLI loads
`modules/hooks-routing/amplifier_module_hooks_routing/matrix_loader.py` **by
file path** (`importlib.util.spec_from_file_location`) and calls the real
`resolve_matrix_source`. Precedence stays in exactly one home; this change adds
no search-order logic of its own.

**Smaller long-term seam, proposed but not taken (cross-repo, not this lane's
call):** hooks-routing shipping `resolve_matrix_source` somewhere app-cli can
import outright — e.g. a tiny published helper distribution, or app-cli
vendoring the pure function under an explicit sync test. Raised in the PR body
for a maintainer's opinion.

**Two non-obvious things measured while building the seam:**

- The **local cache on this host is stale**:
  `~/.amplifier/cache/amplifier-bundle-routing-matrix-972b0ce7f0cbc2f7` is at
  `99d9b08` (routing-matrix #46) and **has no `resolve_matrix_source`** —
  PR #52 (`d17d03c`) is not in it. So on this very host the feature degrades
  to "no markers" until the bundle is refreshed. That is by design (see
  below), and it is why the before/after demo was run against a fixture home
  carrying the actual upstream `matrix_loader.py` rather than the host's.
- **Loading `matrix_loader.py` by path fails unless the module is registered
  in `sys.modules` first.** It defines a `@dataclass` (`MatrixSource`), and
  `dataclasses` looks its own module up by name while building the class;
  without the registration the decorator dies with an opaque
  `'NoneType' object has no attribute '__dict__'`. Fixed and commented in
  `routing_provenance.py::_load_module`.

**Degradation is deliberate and silent-by-omission.** When the function is
unreachable — old bundle, no bundle, load failure — `resolve_matrix_origins()`
returns `{}` and **no marker is drawn**. An empty result means "provenance
unknown", never "nothing is shadowed". A wrong shadowing claim is worse than
none, and guessing the search order is exactly what the item forbade.

---

## (3) What shipped

- **`amplifier_app_cli/lib/routing_provenance.py`** (new) — locate the cached
  bundle's `matrix_loader.py`, load it, classify discovered dirs into custom vs
  bundle, and return one `MatrixSource` per matrix name. Classifying *where a
  directory lives* is not precedence; the aliasing rules (a "custom" dir that
  really is the bundle dir; the same file reached twice) stay inside
  `resolve_matrix_source`.
- **`commands/routing.py`** — a row marker (`⚠ shadows bundle`, visible in
  every view including `--compact`), a footer naming the file **in use** and
  each file it **suppresses** (one path per line, `soft_wrap` so Rich cannot
  reflow a path into the label column), the same note on `routing show`, and
  `MatrixSource.to_dict()` verbatim in `--format json` — the same field
  vocabulary ell publishes on the capability.
- **`tests/test_routing_shadowing.py`** (new, 15 tests).

### Before / after (fixture home, real upstream `matrix_loader.py` @ `d17d03c`)

Before:

```
$ amplifier routing list
── routing matrices (1 active, 1 disabled) ──
  [off]    balanced  (available)  ← disabled
  [on]  → openai  (active)
```

After:

```
$ amplifier routing list
── routing matrices (1 active, 1 disabled) ──
  [off]    balanced  (available)  ← disabled
  [on]  → openai  ⚠ shadows bundle  (active)

⚠ 1 matrix is shadowed — only the 'in use' file is loaded:
  openai
    in use      ~/.amplifier/routing/openai.yaml
    suppressed  ~/.amplifier/cache/amplifier-bundle-routing-matrix-demo/routing/openai.yaml  (bundle)
```

## (4) Tests, and why they are not vacuous

*(confidence: measured · evidence: `uv run pytest -q -p no:randomly`)*

- **shadowed case** — marks the row, names the winner and the suppressed path.
  Carries an explicit **non-vacuity assertion**: the *same* shadowed tree
  rendered through the pre-change path (provenance forced unavailable) shows no
  marker at all. Without that, the test could pass on code that marks
  everything.
- **unshadowed unchanged** — not "looks the same": the output string is
  compared **byte-for-byte** against the same tree rendered with provenance
  unavailable. Text *and* JSON.
- **no user routing dir at all** — `~/.amplifier/routing` absent; command exits
  0, lists both bundle matrices, output identical to baseline.
- **old-bundle degradation** — bundle without `matrix_loader.py`, and a
  `matrix_loader.py` present but lacking the function: no marker, no error.
- **the function really comes from the bundle** — asserted via
  `fn.__code__.co_filename`, so a future accidental local fallback fails loudly.

**Honest limitation of the test fixture, stated rather than hidden.** Nothing
importable exists in a test environment (the whole finding above), so the tests
write a **stand-in** `matrix_loader.py` reproducing the contract at
routing-matrix `d17d03c`. It exercises the CLI's real consumption path —
locate the bundle, load by file path, call, render — but it is a test double,
not upstream's code. Two things mitigate it: the before/after demo was produced
against the **actual upstream file** cloned from `d17d03c`, and
`test_loads_the_bundles_own_resolve_matrix_source` pins the loaded function's
`co_filename` to the bundle path. **A contract drift in upstream's
`resolve_matrix_source` would not be caught by these tests** — that is the cost
of the file-path seam, and the argument for the smaller importable seam
proposed above.

Suite: **1588 passed, 1 skipped, 1 xfailed**. `ruff check` clean on all touched
files; the 8 findings in `commands/session.py` are pre-existing at `0d93352`
and unchanged. `ruff format` applied to touched files only (repo baseline is
not format-clean: 28 files would reformat).

---

## Left open (deliberately not fixed here)

`_load_all_matrices()` (`routing.py:153-160`) keys rows by the `name:` field
inside the YAML with **last-write-wins over `sorted(files)`**. That is *not*
the loader's rule — the loader resolves by **file stem** — and it agrees with
it today only by alphabetical accident (`cache` < `routing`). This change reads
provenance by the winning row's file stem, so the marker is correct either way,
but the row-selection defect is untouched. It was flagged in the item's own
description and **deserves its own work item**; fixing it changes which matrix
a listing shows, which is a behavior change beyond this item's acceptance
criterion.
