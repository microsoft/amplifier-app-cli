# DONE-NOTE — lane `teel-routing-cli-identity`

**Item:** `model_performance-teel` (project `model_performance`)
**Repo:** `microsoft/amplifier-app-cli`, branch `lane/teel-routing-cli-identity`
**Parent commit:** `3bb0104`
**Outcome branch:** **A — RESOLVED.** Every deliverable is DONE. Nothing was
recorded NOT-POSSIBLE; the cap did not bind (see Spend).

Per the LANDING STAGE rule, the deliverables that read as "the live system now
behaves X" are satisfied as "X is demonstrated fail-before/pass-after and
shipped as a draft PR". The merge is the manager's next stage; this lane never
merges.

---

## 1. Result in one paragraph

`amplifier routing list|show` told an operator the opposite of the truth about a
live matrix. The cause was **not** the one the item inferred. PR #294 (merged
`8c4ad7a`, 2026-09-02) had already moved the CLI's row **keying** from the
YAML's internal `name:` to the file stem — the identity the runtime uses — so
the "stem disagrees with `name:`" case the item names was fixed before this lane
opened. What #294 left behind was the **existence gate**: a row survived only
`if data and "name" in data`. The runtime never reads `name:` at all, so a
matrix that hooks-routing loads and routes through was dropped from the CLI
entirely — missing from `list` (contributing to neither the active nor the
disabled count) and "not found" in `show`. That is the residual half that
produces otr's measured output, and it still reproduced at `3bb0104`. This lane
keys the CLI's *existence* rule to the loader's own rule, and titles `show`
headings by the stem in effect rather than by a field the runtime never reads.

---

## 2. What was measured

### 2.1 The defect reproduces at the parent commit, byte-for-byte

Tree: the nine matrices the routing-matrix bundle ships (`972b0ce`), plus one
live user matrix `~/.amplifier/routing/anthropic-knob-consistent.yaml` carrying
no `name:` field, with `routing.matrix = anthropic-knob-consistent` in effect.

At `3bb0104` (`docs/lanes/teel-routing-cli-identity/fail-before.txt`):

```
── routing matrices (0 active, 9 disabled) ──
  [off]    anthropic  (available)  ← disabled
  ... (the eight others)
  [off]    quality  (available)  ← disabled

$ amplifier routing show
Matrix 'anthropic-knob-consistent' not found. Available: anthropic, balanced,
copilot, economy, gemini, ollama, openai, openai-knob-consistent, quality
```

Both of otr's measured lines, including the literal `9`. The live matrix is not
merely mis-keyed — it has **no row at all**.

On the branch (`pass-after.txt`), same tree, same commands:

```
── routing matrices (1 active, 9 disabled) ──
  [on]  → anthropic-knob-consistent  (active)

$ amplifier routing show
Matrix: anthropic-knob-consistent
  Knob-consistent anthropic routing.
```

### 2.2 The runtime genuinely does not need `name:` — checked, not assumed

Read directly from the cached bundle at
`~/.amplifier/cache/amplifier-bundle-routing-matrix-972b0ce7f0cbc2f7/`:

| Runtime function | What it requires |
|---|---|
| `resolve_matrix_source(name, ...)` | builds `f"{name}.yaml"`, takes the first hit in `[*custom_routing_dirs, bundle routing/]` |
| `load_matrix(path)` | only that the file parse to a **mapping** (`"Matrix file must contain a YAML mapping"`) |
| `validate_matrix(matrix)` | `general` and `fast` roles; per-role `description` and `candidates` |

`grep '"name"\|'"'"'name'"'"'' matrix_loader.py resolver_class.py` returns
**nothing**. The YAML's `name:` is read nowhere in the runtime. Its own
docstring says so in prose (`matrix_loader.py:41-43`): the resolved name is
"the file stem … **not necessarily the `name:` field declared inside the
YAML**". The CLI's requirement was the CLI's own invention.

### 2.3 Suite, fail-before / pass-after

| | parent `3bb0104` | branch |
|---|---|---|
| `uv run pytest -q` | 2 failed, **1776 passed**, 1 skipped, 13 deselected, 1 xfailed | 2 failed, **1804 passed**, 1 skipped, 13 deselected, 1 xfailed |
| `tests/test_routing_cli_runtime_identity.py` | **12 failed**, 16 passed | **28 passed** |

+28 passing = exactly the new file. No test changed status in either direction.

The **2 failures are pre-existing and unrelated**, present at the parent commit
with the same two ids. Root-caused rather than waved past: the highway exports
`HIGHWAY_TMUX_SOCKET=hw-model-performance` into every lane session, and
`test_ten_lane_highway_socket_default.py::_derived_socket` shells out with no
`env=`, so the `${HIGHWAY_TMUX_SOCKET:-…}` default branch those two tests exist
to exercise is never taken. Proof:

```
$ echo $HIGHWAY_TMUX_SOCKET                                   -> hw-model-performance
$ uv run pytest tests/test_ten_lane_highway_socket_default.py -q   -> 2 failed, 9 passed
$ env -u HIGHWAY_TMUX_SOCKET uv run pytest … -q                    -> 11 passed
```

Filed as `model_performance-etuz`, linked discovered-from this item. Not fixed
here: that file is outside this lane's owned paths, and CI does not see it (no
`HIGHWAY_TMUX_SOCKET` on GitHub runners), so it is green there and red only for
the lanes that actually run it.

### 2.4 No regression for the common case — stash-compare, byte-identical

Nine invocations (`list`, `list --format json`, bare `show`, `show <stem>`,
`--detailed`, `--compact`, a custom matrix, the not-found path, and `use`)
against an agreeing tree, captured at the parent commit and on the branch at a
fixed console width:

```
parent: 6497 bytes   branch: 6497 bytes
diff -u before after  ->  BYTE-IDENTICAL
```

Captures committed as `byte-identity-parent.txt` / `byte-identity-branch.txt`;
re-verified after `ruff format`. Titling by stem is a no-op *exactly* when stem
and `name:` agree, which is why the common case cannot move.

---

## 3. The change

`amplifier_app_cli/commands/routing.py`, three edits, reporting path only:

1. **`_load_all_matrices_with_paths`** — the existence gate becomes the
   loader's own rule. `if data and "name" in data` → `if isinstance(data, dict)
   and data`. A matrix exists for the CLI exactly when the runtime can load it.
2. **`_disagreeing_name(data, stem)`** (new) — returns the declared name only
   when it is **present and different**. Absence is not disagreement: the
   runtime never reads the field, so warning about its absence would send an
   operator hunting a problem that does not exist — the same wasted trip the
   "not found" message caused. A genuinely mismatched `name:` still warns
   exactly as #294 made it.
3. **`_resolved_identity(data, stem)`** (new), threaded into
   `_show_matrix_resolution` / `_show_matrix_details` — headings name the stem
   the runtime resolved. Previously `matrix_data.get("name", "unknown")`, which
   printed a string the runtime never reads and rendered as the actively
   misleading `Matrix: unknown` for a file with no `name:`. The one caller with
   no stem to offer — the unsaved working copy inside `routing edit` — passes
   `None` and keeps its previous heading.

**Which candidate fix, and why.** The item offered three. This is **(1),
argued from the code**: the CLI now derives *both* keying and existence from
the runtime's rule, so there is one source of truth rather than two that agree
by convention. **(3) — validate at load time and warn — is already present and
was kept**, narrowed so it fires on real disagreement only. **(2) was
rejected**: reporting both identities as co-equal implies the declared name has
standing in resolution, and it has none.

---

## 4. Deliverables

| Deliverable | State |
|---|---|
| Live matrix whose stem differs from its `name:` reports PRESENT and ACTIVE, with a fail-before test on the parent commit | **DONE** — §2.1. Note the stem-vs-`name:` case was already fixed by #294; the case that still reproduced, and that this lane fixes, is the file carrying no `name:` at all. Both are pinned. |
| CLI and runtime cannot diverge, pinned by a test; no cross-repo edit | **DONE** — `TestCliAndRuntimeCannotDiverge` asserts the CLI's row set equals the set `resolve_matrix_source()` resolves, obtaining that function through production's own `load_resolve_matrix_source()`. It asks the runtime rather than re-deriving its rule, so it cannot be satisfied by teaching the test the same wrong answer as the code. A non-vacuity case pins that an unresolvable stem is absent from both sides. No file outside this repo was touched. |
| No regression for the common case, pinned | **DONE** — §2.4 (stash-compare) plus `TestAgreeingTreeUnchanged`, which holds the agreeing-tree listing as an exact golden literal. |
| Runtime selection UNCHANGED | **DONE** — `TestRuntimeSelectionUnchanged`: settings bytes are unchanged across `list`/`show`, and `resolve_winning_paths` (which decides *which file* a stem loads) returns the same winner per stem. The change alters which rows survive, never which file a row points at, and never what `routing.matrix` holds. |
| Full suite green, pasted in PR body; fail-before transcript from the parent | **DONE, with a stated caveat** — §2.3. Green except two pre-existing, root-caused, unrelated failures that are equally red at the parent commit. Reported rather than hidden, and filed as `model_performance-etuz`. |
| Draft PR on origin | **DONE** — see the marker's `publication` block for the values read back from the remote. |
| DONE-NOTE at `docs/lanes/teel-routing-cli-identity/DONE-NOTE.md` | **DONE** — this file (artifact-path/v1; repo root never touched). |

---

## 5. The cross-repo boundary — reported, not crossed

The runtime identity lives in `matrix_loader.py` in
**amplifier-bundle-routing-matrix**, a different repo. Nothing there was edited.

**What a matching change in that repo would need to be: none.** The runtime's
rule was already correct and already documented (`matrix_loader.py:41-43`); the
CLI was the half that disagreed with it, and the CLI is the half that moved.
This lane pins the CLI against that documented keying.

Two things that repo *could* do, neither required for this fix:

- **Publish the identity rule as a function rather than as a docstring.** The
  CLI currently learns "the stem is the identity" by loading
  `resolve_matrix_source` and by prose. A tiny exported helper (e.g.
  `matrix_identity(path) -> str`, returning `path.stem`) would let the CLI
  import the rule instead of mirroring it, and the pin in
  `TestCliAndRuntimeCannotDiverge` could assert against the import directly.
- **Warn at load time when `name:` disagrees with the stem** — the item's
  candidate (3), on the runtime side. The CLI now warns; the runtime is still
  silent, so a user who never runs `routing list` never learns the field is
  decorative.

Both are suggestions for that repo's owner. Neither blocks this change, and
neither was attempted from here.

---

## 6. Spend

**Authority: $0.00** — arithmetic as stated in the goal, `0 runs × 0 arms ×
$0.00 / 1.00 = $0.00`, slack $0.00. The arithmetic closes: this deliverable
buys no runs, no arms, and no validity-gated measurements, so a $0 authority is
correctly sized for it rather than mis-sized.

**Spent: $0.00.** No API calls, no containers, no DTU, no eval runs. Pure
code-and-test change verified with the repo's own pytest suite and a
stash-compare. No infrastructure was created, so no `infra_ledger.sh` row was
opened and no teardown was needed. The cap never bound; no deliverable was
dropped or shrunk.

---

## 7. Choices recorded (no human was waited on)

1. **Fixed the residual defect rather than reporting the item as already
   fixed.** #294 fixed the keying half; the item's *measured symptom* still
   reproduced at `3bb0104`. The operator-facing lie is the deliverable, so the
   lane fixed the half that still produces it, and says plainly which half was
   already done.
2. **Existence keyed to loadability, not to the stem's mere presence.** A file
   that fails `load_matrix()` — non-mapping, empty, unparseable — still gets no
   row. Pinned by `TestUnloadableFilesStillDropped`. Widening existence past
   what the runtime can load would be the same class of lie in the other
   direction.
3. **Absent `name:` produces no warning.** Warning on a field the runtime never
   reads manufactures a false problem in the exact tool an operator reaches for
   when they already suspect one.
4. **`routing edit`'s working copy keeps its old heading.** An unsaved copy has
   no stem, so inventing one would be worse than the status quo.
5. **The two pre-existing suite failures were root-caused, not waved past** —
   and filed rather than fixed, because the file is outside this lane's owned
   paths.

## 8. What remains open

- `model_performance-etuz` — the `HIGHWAY_TMUX_SOCKET` test-isolation defect
  above. Red for every lane in this repo, green in CI.
- The two optional routing-matrix-side suggestions in §5, for that repo's owner.
- Nothing in this lane's own scope.
