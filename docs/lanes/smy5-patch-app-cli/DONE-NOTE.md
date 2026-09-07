# Lane `smy5` — the lean always-on head lands in app-cli

**Item:** `model_performance-smy5` (project `model_performance`)
**Repo:** `microsoft/amplifier-app-cli`, branch `lane/smy5-patch-app-cli`
**Outcome:** **A. RESOLVED** — every deliverable DONE. No cap-bound
NOT-POSSIBLE, no blocker.
**Spend:** **$0.00** against a **$0.00** authority (`0 runs × 0 arms × $0 / 1.00
= $0.00`, slack `$0.00`). Zero API calls, zero DTUs, zero containers, nothing
registered in the infra ledger and therefore nothing to tear down.
`lane_teardown.sh` was not run; `infra_ledger.sh sweep` was never run.
The arithmetic closes trivially because this lane buys no runs: it applies a
patch that was already measured and paid for (`g7h3`, $428.10) and runs a local
test suite. **No API measurement was authorised and none was performed.**

---

## The one-line result

`context/cli-awareness.md`, which renders into the **always-on head** and is
therefore paid for on **every request of every session**, went from
**398 → 338 characters (404 → 342 bytes), saving 60 characters** — applied at
**zero fuzz**, byte-identical to the upstream artifact, fidelity re-verified at
today's head with **nothing dropped**, and pinned by 13 tests that fail on the
old text.

---

## Deliverables

| # | Deliverable | State |
|---|---|---|
| 1 | Patch applied — no fuzz, divergence named | **DONE** (`patch -p3 -F0`, see §2) |
| 2 | Fidelity table re-verified at **today's head**, not inherited | **DONE** (§3) — nothing dropped |
| 3 | Stock → lean char counts for every file touched | **DONE** (§4) |
| 4 | Pin test so the text cannot drift back | **DONE** (§5) — 13 tests, fail-before proven |
| 5 | CI green where the repo has CI | **DONE locally** (§6); this repo **does** have CI — confirmed on the PR |
| 6 | DRAFT PR, not merged | **DONE** — manager merges |
| 7 | DONE-NOTE at the lane artifact root | **DONE** (this file) |

---

## 1. Scope: exactly one file in this repo, and that was verified, not assumed

`zc6t` shipped **19 patch artifacts** (9 context-file + 10 tool-description).
I read the target path out of **every one** rather than trusting the goal's
summary:

```
context-files/00 → context/gitea-awareness.md          (bundle repo)
context-files/01 → context/dtu-awareness.md            (bundle repo)
context-files/02 → context/amplifier-tester-awareness.md (bundle repo)
context-files/03 → context/modes-instructions.md       (bundle repo)
context-files/04 → amplifier_app_cli/_bundle/context/cli-awareness.md   <-- THIS REPO
context-files/05 → context/skills-instructions.md      (bundle repo)
context-files/06 → context/wayfinder-voice.md          (bundle repo)
context-files/07 → context/propose-and-ack.md          (bundle repo)
context-files/08 → context/routing-instructions.md     (bundle repo)
tool-descriptions/*.patch → <tool>.description         (amplifier-module-tool-* / bundle repos)
```

**Exactly one of the nineteen targets this repo.** Nothing else was touched, and
no file outside this worktree was written.

Two items in `zc6t`'s report are **explicitly out of this repo's scope** and are
named here so they are not silently assumed handled:

- **The one REAL weakening — `edit_file` (`missing_rules: ["ALWAYS"]`)** — lives
  in `amplifier-module-tool-filesystem`, not here. `zc6t` restored it in-repo at
  +450 chars and pinned it. **Not this lane's file; carried forward by whichever
  lane owns that repo.** I did not touch it and do not claim it.
- **`web_search` and `mode` are verified byte-identical no-ops** (`zc6t` F2/F3).
  Both live outside this repo. **No file was edited here to make a count match.**
- **The two hook-generated `<system-reminder>` blocks** (861 + 5,350 chars) were
  **not** hand-edited. No source file in this repo produces them; that is
  `model_performance-z6wa`.

---

## 2. The apply — and the divergence, which is real

**`patch -p1` FAILS.** The artifact names the *installed wheel* layout, not the
*source* layout:

```
$ patch -p1 -F0 --dry-run < 04-__app_cli__-cli-awareness.md.patch
can't find file to patch at input line 3
|--- a/amplifier_app_cli/_bundle/context/cli-awareness.md
|+++ b/amplifier_app_cli/_bundle/context/cli-awareness.md
Perhaps you used the wrong -p or --strip option?
rc=1
```

**Why:** `zc6t` measured the *resolved runtime path*
(`.../site-packages/amplifier_app_cli/_bundle/context/cli-awareness.md`), which
is where `pyproject.toml`'s hatch `force-include` block **puts** the file in the
built wheel:

```toml
[tool.hatch.build.targets.wheel.force-include]
"context" = "amplifier_app_cli/_bundle/context"
```

`amplifier_app_cli/_bundle/` **does not exist in the source tree** — it is a
build artifact. The source of truth is the repo-root overlay, `context/`. This
is also why patch 04 is the only one of the nineteen whose path is not
`context/<name>.md`: it is the only target that was read out of an installed
wheel rather than a cloned repo.

**Resolution — a prefix strip, NOT a fuzz.** Stripping the three build-layout
components (`a/`, `amplifier_app_cli/`, `_bundle/`) lands on the real source path:

```
$ patch -p3 -F0 --dry-run < 04-__app_cli__-cli-awareness.md.patch
checking file context/cli-awareness.md
rc=0
```

**`-F0` is load-bearing: zero fuzz permitted, and none was needed.** The hunk
matched exactly. This follows the `l4s1` precedent — `l4s1` saw
*"Hunk #1 succeeded at 56 with fuzz 2"* and hand-ported rather than accept a
silent placement decision. Here the context matched at fuzz 0, so no hand-port
was warranted; the only adjustment is the `-p` level, which is a **declared path
mapping, not a guessed placement**.

**Independent verification that `-p3` was the right level** — because the goal
warns that a wrong `-p` can exit 0 with confident, wrong output. Three checks,
all against values known before the apply:

1. `patch` named the file it touched: `context/cli-awareness.md` — the one file
   `grep -rn cli-awareness` shows this repo has.
2. The result is **byte-identical** to `zc6t`'s independently-shipped
   `.lean.md` artifact (`diff` clean) — a value produced by a different process
   than the patch.
3. Post-apply character count is **338**, matching `fidelity-report.json`'s
   `lean_chars: 338` exactly.

---

## 3. Fidelity re-verified at TODAY'S head — nothing dropped

**`zc6t`'s table was not inherited.** I re-derived stock from
`git show HEAD:context/cli-awareness.md` in this worktree and re-ran the check.

**First: the file has not drifted since `zc6t` measured it.** Today's stock is
**398 characters**, exactly `fidelity-report.json`'s `stock_chars: 398`. The
repo copy and the installed wheel copy are also `diff`-identical, so the
artifact was measured against the same text this repo ships.

**Mechanical check — every content word in stock, is it in lean?**

```
STOCK content-words absent from LEAN:
  ['are', 'running', 'its', 'and', 'carries', 'if', 'question', 'about']
```

All eight are **function words absorbed by the rewrite** (`You are running
inside` → `You run inside`; `that carries` → `carrying`; `If the question is
about *your own task*` → `Your own task`). **Zero rules, constraints, commands,
or pointers lost.**

**Structural-carrier check** — the classes a shrink usually drops:

| carrier | stock | lean | verdict |
|---|---|---|---|
| fenced code blocks | 0 | 0 | OK |
| bullets | 0 | 0 | OK |
| headings | 1 | 1 | OK |
| tool-call forms `f(...)` | 0 | 0 | OK |
| `@mentions` | 0 | 0 | OK |
| backtick spans | 0 | 0 | OK |
| URLs | 0 | 0 | OK |

**There was no structural carrier here for a shrink to drop.** That is recorded
as a checked fact, not an assumption — `test_no_structural_carriers_lost` pins
it so it stays true.

**Semantic check — the four load-bearing claims, all present in lean:**

| # | claim | in lean? |
|---|---|---|
| 1 | an expert exists at all | yes |
| 2 | its domain is enumerated: commands, flags, config, session machinery | yes (all four) |
| 3 | the docs are **authoritative** *and* **version-matched** | yes (both) |
| 4 | the negative boundary — the user's own task is **not** this domain | yes |

**MISSING RULES: NONE.** This matches `fidelity-report.json`'s
`"missing_rules": []` for index 4 — re-derived here, not copied.

The only losses are **markdown emphasis markers** (`**Amplifier CLI
application**`, `*your own task*`). Emphasis is not a rule, and it is part of
the measured saving.

---

## 4. Stock → lean character counts

| file | stock | lean | saved |
|---|---|---|---|
| `context/cli-awareness.md` | **398 chars** (404 bytes) | **338 chars** (342 bytes) | **−60 chars** (−15.1%) |

**One file touched; this table is complete.**

**Characters vs bytes — stated so the two numbers are never confused.**
`fidelity-report.json` counts **characters**; `wc -c` reports **bytes**. The
file contains three em-dashes (`—`, 3 bytes each in UTF-8), so bytes exceed
characters by 6 in stock and 4 in lean. **398 chars = 404 bytes** and
**338 chars = 342 bytes** are the same measurements in two units, not a
discrepancy. The saving is **60 characters / 62 bytes**.

**Why it is worth a PR at all:** `behaviors/cli-expertise.yaml:29` includes
`app-cli:context/cli-awareness.md`, so this text is `context.include` content —
**paid for on every request of every session, whether or not the CLI-expert
capability is ever used.** `g7h3` measured the full lean head at **−13.57%
$/task, CI [−22.27%, −4.86%]**, all three pre-registered estimators excluding
zero.

---

## 5. The pin test — `tests/test_cli_awareness_lean_pin.py`

A prose file has no compiler. Unpinned, the v1 wording returns the first time
someone "improves the docs" and the saving evaporates with nothing going red.
**13 tests** in five layers:

1. `test_text_is_byte_exact` — exact anti-drift pin.
2. `test_char_budget` — the head is a budget; the assertion reports the delta
   *per request*, so growth is a visible decision.
3. `test_semantic_invariants_survive` (×4, parametrised) — the fidelity half. A
   **future further shrink is welcome**; dropping a load-bearing claim is not.
   This layer survives a rewrite the byte-exact test would reject, so it still
   guards the next lean pass.
4. `test_v1_prose_has_not_returned` (×5) — names the exact v1 fragments, so a
   revert fails with a legible reason rather than an opaque diff.
5. `test_no_structural_carriers_lost` + `test_pinned_file_exists_where_the_behavior_expects_it`
   — the second catches the *other* way the saving can be lost: the file being
   moved or dropped from `cli-expertise.yaml`, leaving the pin green while the
   pointer goes missing from the head.

**FAIL-BEFORE, run against the stock text** (`evidence/fail-before-pin-test.txt`):

```
8 failed, 5 passed in 0.06s
FAILED test_text_is_byte_exact
FAILED test_char_budget
FAILED test_semantic_invariants_survive[the negative boundary: the user's own ta]
FAILED test_v1_prose_has_not_returned[You are running inside the **Amplifier CLI application**]
FAILED test_v1_prose_has_not_returned[its commands, flags, config, and session machinery]
FAILED test_v1_prose_has_not_returned[dedicated expert that carries the authoritative]
FAILED test_v1_prose_has_not_returned[If the question is about *your own task*]
FAILED test_v1_prose_has_not_returned[that is not this domain — handle it normally]
```

**PASS-AFTER** (`evidence/pass-after-pin-test.txt`): `13 passed in 0.01s`.

**Windows note:** the repo ships no `.gitattributes`, so a windows-latest
checkout can materialise CRLF. The test normalises newlines before comparing, so
the pin is about **content drift**, not about the checkout's line-ending policy —
otherwise it would go red on Windows CI for a reason unrelated to drift.

---

## 6. CI and the test suite — including one failure I am not hiding

**This repo HAS CI** (`.github/workflows/ci.yml`): a `test` job
(ubuntu/macos/windows × py3.11/3.12, full suite, no deselects) and a separate
`integration` job (ubuntu/macos, `-m integration`). Both are exercised locally.

| run | result |
|---|---|
| Full suite (default job equivalent) | **1907 passed, 1 skipped, 13 deselected, 1 xfailed** |
| Integration job (`-m integration`) | **13 passed, 1909 deselected** |

Stock suite before this change was 1894 passing; **+13 is exactly the new pin
test.** Nothing else moved.

**HONEST DISCLOSURE — the first full-suite run had 1 failure, and it was not
mine.** `tests/test_truststore_wrap_bio_shim.py::test_real_truststore_is_covered_at_cli_import`
failed with `status was: 'skipped: truststore is not importable'`. Rather than
assume, I proved it independent of my change:

1. **Stashed my change**, ran that file at clean `HEAD` → **11 passed**.
2. Restored my change, re-ran the **full suite** → **1907 passed, 0 failed**.
3. Ran the suite a **third** time → **1907 passed, 0 failed**.
4. `uv run python -c "import truststore"` → imports fine from the lane `.venv`.

**Cause:** a transient venv state during a `uv` re-sync (`uv run` reported
"Uninstalled 1 package / Installed 1 package" around that window) — the test
correctly detected `truststore` being momentarily absent. It is **environmental
and unrelated to a markdown file**; a context-file edit cannot influence
truststore importability. Recorded rather than quietly re-run into a green.

**CI green is claimed only where it is real.** The runs above are **local**.
GitHub Actions CI was then confirmed on the PR itself — **all 9 checks pass** on
`84e3f99` (run `34146578224`, PR
[#320](https://github.com/microsoft/amplifier-app-cli/pull/320)):

```
pytest (ubuntu-latest,  py3.11)   pass  33s
pytest (ubuntu-latest,  py3.12)   pass  32s
pytest (macos-latest,   py3.11)   pass  38s
pytest (macos-latest,   py3.12)   pass  35s
pytest (windows-latest, py3.11)   pass  57s
pytest (windows-latest, py3.12)   pass  1m2s
pytest -m integration (ubuntu-latest)  pass  39s
pytest -m integration (macos-latest)   pass  45s
license/cla                            pass
```

**Both windows legs pass**, which is the live confirmation that the pin test's
newline normalisation does the job — a byte-exact pin on a file in a repo with
no `.gitattributes` is exactly the shape that goes red on Windows for the wrong
reason, and it does not.

The PR was marked **ready for review only after** this green, per the goal.
It remains **unmerged** — the manager merges.

---

## 7. Deviations, and one recommendation for the manager

**Deviation 1 — `-p3` instead of `-p1`.** Documented in §2 with the failing
`-p1` output quoted, the mechanism named (`force-include` build layout vs source
layout), and three independent confirmations that the result is right. **No
fuzz was used or permitted (`-F0`).**

**Recommendation (not actioned — outside this lane's owned paths).** The
`_bundle/` prefix in artifact 04 is a **latent trap for any future lane**: it is
the one artifact that names a wheel path, and a lane that reaches for `-p1`,
fails, and then reaches for higher fuzz instead of a higher `-p` would land the
hunk somewhere plausible and wrong. If `zc6t`'s artifact directory is ever
regenerated, capturing `__app_cli__` targets from the **source** checkout
(`context/cli-awareness.md`) rather than the installed wheel would remove it. I
did not edit `amplifier-foundation` to fix this — that is another repo, and this
lane writes only inside `amplifier-app-cli`.

**No human decision was waited on.** No PII or team-internal data appears in any
output.

---

## Evidence

| file | what it is |
|---|---|
| `evidence/04-__app_cli__-cli-awareness.md.patch` | the upstream artifact, verbatim, as applied |
| `evidence/04-__app_cli__-cli-awareness.md.lean.md` | upstream lean text — the result is byte-identical to it |
| `evidence/fidelity-report-entry-04.json` | `zc6t`'s entry for this file (398 → 338, `missing_rules: []`) |
| `evidence/fail-before-pin-test.txt` | pin test against the stock text: 8 failed |
| `evidence/pass-after-pin-test.txt` | pin test against the lean text: 13 passed |
| `evidence/full-suite.txt` | 1907 passed, 1 skipped, 13 deselected, 1 xfailed |
| `evidence/integration-suite.txt` | 13 passed, 1909 deselected |

**Provenance:** artifacts fetched from `microsoft/amplifier-foundation` at
`4384805741ed7a1a8644adfd6ded9fe1ff4b4a5a` (foundation `main`) — the full SHA
read back from the API, matching the goal's stated `4384805` (PR #372).
