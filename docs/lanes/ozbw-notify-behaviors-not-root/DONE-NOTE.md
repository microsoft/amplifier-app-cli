# Lane `ozbw` — compose the notify BEHAVIORS, not the notify ROOT

**Item:** `model_performance-ozbw` · **Repo:** `microsoft/amplifier-app-cli` ·
**Branch:** `lane/ozbw-notify-behaviors-not-root` ·
**PR:** [#316](https://github.com/microsoft/amplifier-app-cli/pull/316) (READY, not draft) ·
**Head:** `9ff57c3f01e44dd846325580299e9ce3a8f9bf15`

**Outcome: branch A — RESOLVED.** All eight deliverables DONE. Nothing NOT-POSSIBLE,
nothing blocked. Spend authority `$0` was correct and was not exceeded.

---

## 1. Deliverables

| # | Deliverable | State |
|---|---|---|
| 1 | `_build_notification_behaviors()` returns ONLY behavior URIs, never a bare root; pinned by a test | **DONE** |
| 2 | `amplifier update` SHA motivation preserved, fail-before reproduced first | **DONE** |
| 3 | #315 guard tests still pass; guard untouched; stated in the PR body | **DONE** (5/5) |
| 4 | Real-session check + `amplifier update --check-only` real SHA | **DONE** |
| 5 | Audit the rest of `config.py` for the same pattern | **DONE** (0 remaining) |
| 6 | Update notify PR #10's description; do not merge | **DONE** |
| 7 | PR left READY with a verification comment | **DONE** ([comment](https://github.com/microsoft/amplifier-app-cli/pull/316#issuecomment-5559861684)) |
| 8 | Full suite green, pasted in the PR body | **DONE** (1853 passed) |

---

## 2. The change

One deletion in `amplifier_app_cli/runtime/config.py`:

```diff
-    # Root bundle first — a minimal marker that just identifies the repo
-    # and ensures the bundle gets cached with proper SHA metadata (fixes
-    # the "unknown" version issue during `amplifier update`). The actual
-    # functionality comes from the subdirectory behaviors below.
-    behaviors.append("git+https://github.com/microsoft/amplifier-bundle-notify@main")
```

plus a docstring recording *why* the removal is safe, and 12 new tests.

---

## 3. The hard part — the `amplifier update` SHA motivation

This is deliverable 2, and the goal was right to make it the gate.

### What the original bug actually was

`git log -L` on the line leads to **`2fa2c47`** — *"fix: resolve 'unknown' SHA for bundles
during update check"*. Its own message says the update check "looks for ROOT bundle cache
entries. Since only subdirectory entries were cached, the root bundles showed 'unknown'".
The same commit added the root URI to **both** `_build_notification_behaviors()` and
`_build_modes_behaviors()`.

**`modes` already lost its root URI**, in `91e1beb` (*"load modes behavior only, not root
bundle"*), with no replacement for the SHA motivation and no reported regression. notify
was the last survivor. That was the first hint the stated motivation did not hold.

### Why it does not hold

`amplifier update` never depended on the composition. It resolves `notify` through
`WELL_KNOWN_BUNDLES` (`discovery.py:82`, which registers the **root** remote URI and is
added unconditionally by `list_cached_root_bundles()` step 2), then asks
`GitSourceHandler.get_status()` for the root URI's cached commit
(`commands/update.py::_check_bundle_uri`).

And `GitSourceHandler._get_cache_path()` is:

```python
cache_key = sha256(f"{git_url}@{ref}".encode()).hexdigest()[:16]
```

`git_url` comes from `_build_git_url()` = `scheme://host/path` — **the `#subdirectory=`
fragment is not in the key.** Measured:

```
ROOT     cache_path = amplifier-bundle-notify-a1c452253c0fb2e2   subpath=''
DESKTOP  cache_path = amplifier-bundle-notify-a1c452253c0fb2e2   subpath='behaviors/desktop-notifications.yaml'
PUSH     cache_path = amplifier-bundle-notify-a1c452253c0fb2e2   subpath='behaviors/push-notifications.yaml'
```

One clone, one metadata file, one `git rev-parse HEAD`. Fetching a behavior fills the
entry the root's status check reads. The root composition contributed **nothing**.

### Fail-before / pass-after — and an honest note on what "before" can mean

The deliverable asks for the symptom "with the root line removed and *no* replacement".
**That state is not reachable in the running system**: remove the root line and the two
behavior URIs are still composed, and they fetch the repo. So the honest report is —

> The symptom does **not** reproduce with the root line removed, because the behavior
> fetch already populates the same cache entry. No replacement was needed, and none was
> written.

Which is a stronger result than "I found a replacement", but only if the symptom itself is
demonstrated rather than asserted. It was, three ways, all with the **real** repo or a
real git repo, never a mock:

**(a) Live, real CLI, real repo, isolated `AMPLIFIER_HOME`** — `evidence/live-update-sha.txt`

```
BEFORE (nothing from the notify repo fetched):
│ modes  │ unknown │ 3e2a9e6 │ ● │
│ notify │ unknown │ 7f5ff46 │ ● │     <-- the reported symptom, reproduced

… fetch ONLY the two #subdirectory=behaviors/*.yaml URIs; the ROOT is never resolved …

AFTER (same command, same isolated home):
│ modes  │ unknown │ 3e2a9e6 │ ● │     <-- CONTROL, unchanged
│ notify │ 7f5ff46 │ 7f5ff46 │ ✓ │     <-- real SHA
```

The `modes` control matters: it rules out "the second invocation just works". `modes` is
behavior-only in code and nothing fetched it in this experiment, so it stays `unknown`
while `notify` moves. The only difference between the two rows is the fetch.

**(b) End-to-end through the changed code** — `evidence/e2e-branch-update-sha.txt`.
Feed `_build_notification_behaviors(desktop=True, push=True)`'s own output into a fresh
isolated cache, assert no bare-root URI leaked, fetch exactly those URIs, then run the
real `amplifier update --check-only`: `notify │ 7f5ff46 │ 7f5ff46 │ ✓`.
Same result against the real `~/.amplifier` from the branch build.

**(c) Hermetic, no network** — `evidence/fail-before-pass-after-hermetic.txt`, and now a
test. A throwaway git repo shaped like notify (root `bundle.md` **with** a body):

| state | root's `cached_commit` |
|---|---|
| nothing fetched | `unknown` (None) |
| behaviors only, root never composed | `571599d6…` = real HEAD |
| control: root + behaviors (today's code) | `571599d6…`, byte-identical |

### The invariant is now pinned, not assumed

Removing the workaround means depending on foundation's cache key staying
fragment-insensitive — a cross-repo assumption. So it is a test, with the failure message
saying exactly what broke and what to do:
`test_behavior_uri_and_root_uri_share_one_git_cache_entry`. If amplifier-foundation ever
keys on the fragment, this repo fails loudly instead of silently reporting `unknown`.

---

## 4. Real-session check (deliverable 4)

`amplifier run "hi" -B anchors-amp-dev`, same host, same `~/.amplifier` settings
(`notifications.desktop.enabled: true`, `notifications.ntfy.enabled: true`), scratch build
(`uv run`) of each tree. `evidence/real-session-before-after.txt`.

| | BEFORE — worktree at `dab0f7c` | AFTER — this branch |
|---|---|---|
| session | `10d7a2e8-1295-481f-881d-c4c5f95c92e7` | `05457635-b553-4559-8815-55306e3bfd59` |
| `raw.system` chars | 97,978 | 98,339 |
| first line | `@anchors-amp-dev:context/system.md` | same |
| marker `configured for development OF` | **true** | **true** |
| `# Notify Bundle` anywhere | **false** | **false** |
| `<context_file>` blocks | **23** | **23** |
| `mentions:resolved` | 23 | 23 |
| **#315 drop-warning naming `amplifier-bundle-notify`** | **1** | **0** |

`amplifier update --check-only` from the branch build against the real home:
`notify │ 7f5ff46 │ 7f5ff46 │ ✓`.

**Reading this honestly.** `raw.system` is already correct on `dab0f7c` — that is #315
doing its job, and this lane must not claim credit for it. The line that moves is the
last one: on `dab0f7c` the guard fires **every session**, because notify's root body still
enters `compose()` and has to be dropped. After this change it never enters compose at all,
so there is nothing to drop and users stop seeing that warning. That is precisely the
deliverable's *"it is no longer even entering compose"*.

23 = the 22 app-bundle context includes + the root bundle's own
`@anchors-amp-dev:context/system.md`, matching f26u's measured `mentions:resolved = 23`.

---

## 5. Audit (deliverable 5) — one bug, not a pattern, and now guarded

`evidence/config-audit.txt`. Every always-on behavior builder, checked by **running** it:

| builder | verdict |
|---|---|
| `_build_modes_behaviors` | behavior-only (fixed earlier, `91e1beb`) |
| `_build_skills_behaviors` | behavior-only |
| `_build_wayfinder_behaviors` | behavior-only |
| `_build_app_cli_behaviors` | behavior-only (`file://…#subdirectory=behaviors/cli-expertise.yaml`) |
| `_build_notification_behaviors` | **was the last root-composer — fixed here** |

**Bare-root URIs composed as behaviors: 0.** Four of the five already carried a docstring
saying "only the behavior, NOT the root bundle.md"; notify was the one that said the
opposite and did the opposite. The finding is therefore *"it was a known hazard with no
enforcement"* — so enforcement is what was added:
`test_no_always_on_behavior_builder_composes_a_bare_root_bundle` covers all five.

The sixth `compose_behaviors` source is the user's own `bundle.app` list
(`config.py:127`). That is user-authored and not app-cli's to police — and it is exactly
what #315's guard protects. Left alone, deliberately.

---

## 6. Tests and suite

New `tests/test_notify_behaviors_not_root.py` — **+12**, covering: no bare root across all
three flag combinations; every URI carries `#subdirectory=behaviors/` and ends `.yaml`;
exact per-combination lists; the five-builder pattern guard; the shared-cache-path
invariant; and the hermetic end-to-end fail-before/pass-after.

The end-to-end test builds a `git+file://` URI, so it carries
`skipif(sys.platform == "win32")` with the reason stated in the decorator (a `C:/…`
drive-letter path is not expressible in `_build_git_url()`'s `scheme://host+path` split),
and notes that the same invariant is pinned platform-independently by the
cache-path test. CI runs Windows and macOS; nothing else in the change is
platform-dependent.

Two stale comments in `tests/test_notification_hook_overrides_defaults.py` ("composes the
root bundle + …") were corrected. **No assertion in that file changed** — its checks were
already `any(... in b ...)`, which is why they did not catch this.

| | baseline (worktree at `dab0f7c`) | this branch |
|---|---|---|
| `uv run pytest -q` | 1841 passed, 1 skipped, 13 deselected, 1 xfailed | **1853 passed**, 1 skipped, 13 deselected, 1 xfailed |

Delta = +12, all new, all passing. `evidence/full-suite.txt`.

**The known failure, reported rather than hidden.** An earlier full-suite run of **both**
trees produced one failure —
`tests/test_truststore_wrap_bio_shim.py::test_real_truststore_is_covered_at_cli_import`,
*"skipped: truststore is not importable"* — at 1 failed/1840 (baseline) and 1 failed/1852
(branch). Identical failure, identical cause, present with and without this change; it is
the collection-order dependency the goal names, and it passes in isolation (11 passed). It
did not reproduce on the recorded capture. Either way the failure set is the same in both
trees and the only delta is +12 passes. Not absorbed.

**Baseline method:** a `git worktree` at HEAD rather than `git stash`, so the comparison
tree is a real independent checkout and there is no window in which the work could be lost.

---

## 7. Spend

**Authority: `$0` — arithmetic `0 runs × 0 arms × $0 / 1.00 = $0.00`, slack `$0.00`.**

Checked on first read, as the authoring rule requires: the arithmetic **closes**. This
item buys zero runs; it is a pure code/test change. There is no validity rate to divide
by and nothing to under-fund. **No branch-B finding on cap sizing.**

| Item | Cost |
|---|---|
| API/DTU purchases against the authority | **$0.00** |
| `amplifier run "hi"` ×2 — BEFORE $0.27, AFTER $0.75 | $1.02 (local verification invocations) |
| `amplifier update --check-only` ×4, hermetic probes, full suite ×4 | $0.00 (no LLM) |

The two `amplifier run "hi"` invocations are the deliverable-4 real-session check. Per the
item's own wording these are normal local invocations, not purchases, and are recorded
anyway — same treatment as lane f26u ($1.02 for the same two calls, coincidentally
identical).

**Residue:** the authority is `$0.00` and nothing needed buying, so there is no unspendable
residue to report. **Infrastructure created: none** — no DTU, no Gitea, no containers. No
`infra_ledger.sh` rows, nothing to tear down. `sweep` was never run.

---

## 8. Deviations and corrections, all named

1. **PR is READY, not draft.** Procedure step 4 says `gh pr create --draft`; the
   DELIVERABLES list says *"A PR left READY (not draft) with a verification comment"* and
   explains why (owner pre-authorized admin merge; manager merges next cycle after
   re-running the fail-befores). The item-specific deliverable is the more specific
   instruction, so the PR is open and ready. Recorded here as the goal requires.
2. **Correction to the goal's KNOWN section.** It states *"this host's installed CLI is a
   git install pinned at `6afcae3d`"*. It is not: `amplifier --version` reports
   `2026.09.06-dab0f7c (core 1.6.1)` — i.e. f26u's own commit, **with** the #315 fix. This
   changed nothing about the work (the scratch builds are what was measured), but the note
   as written would mislead the next reader. Consistent with it: the BEFORE session shows
   `# Notify Bundle` **absent** and the drop-warning **present**, which is the fixed
   build's signature, not the broken one's.
3. **The fail-before is the symptom, not the literal state.** Stated in §3 rather than
   quietly satisfied: "root line removed, no replacement" is unreachable, so what is
   reproduced is the underlying `unknown`, plus a control proving the behavior fetch is
   what clears it.
4. **`_preserve_root_instruction()` untouched.** Not weakened, not removed, still five
   passing tests. Said explicitly in the PR body, as the deliverable requires.
5. **notify PR #10 not merged.** Only its description was edited, to record that after
   #316 the root body is inert twice over and #10 is hygiene rather than a fix. No file in
   `amplifier-bundle-notify` was touched. (`gh pr edit` emitted a Projects-classic
   deprecation error and did **not** write; the edit was completed with
   `gh api -X PATCH` and then read back to confirm it landed.)

---

## 9. Open, for whoever picks this up

1. **`modes`, `skills`, `wayfinder` and `routing-matrix` can report `unknown` in
   `amplifier update` on a fresh install** — visible in `evidence/live-update-sha.txt`,
   where every uncached bundle shows `unknown`. That is the *general* form of the bug
   `2fa2c47` was chasing, and it is orthogonal to this change: a bundle whose repo has
   never been fetched has no cached commit. Whether `amplifier update` should show
   `not cached` rather than `unknown` there is a display question this lane did not touch.
2. **f26u's §6 follow-ups still stand** — the composed bundle inheriting the last
   behavior's `name`/`base_path`, and the owner-last compose ordering. Neither is affected
   by this change.

---

## 10. Files

```
amplifier_app_cli/runtime/config.py                      (the change + rationale docstring)
tests/test_notify_behaviors_not_root.py                  (new, +12 tests)
tests/test_notification_hook_overrides_defaults.py       (2 stale comments corrected)
docs/lanes/ozbw-notify-behaviors-not-root/DONE-NOTE.md   (this file)
docs/lanes/ozbw-notify-behaviors-not-root/evidence/
    live-update-sha.txt                  live CLI fail-before/pass-after, real repo
    e2e-branch-update-sha.txt            end-to-end through the changed code
    fail-before-pass-after-hermetic.txt  hermetic 3-state probe + cache-path identity
    real-session-before-after.txt        amplifier run "hi", before/after
    config-audit.txt                     all five builders, verdict per URI
    full-suite.txt                       baseline vs branch, plus the known failure
```
