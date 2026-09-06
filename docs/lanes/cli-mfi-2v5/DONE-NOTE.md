# DONE-NOTE — lane `cli-mfi-2v5`

**Items:** `recipes-mfi`, `recipes-2v5` (project `recipes`)
**Repo:** `microsoft/amplifier-app-cli`, branch `lane/cli-mfi-2v5`
**Parent commit:** `569c9b8`
**Outcome:** both defects fixed, fail-before/pass-after measured, full matrix
green locally. The merge is the manager's stage; this lane opens a PR and
stops.

---

## 1. Result in one paragraph

Two independent "the shared state lied to you" defects, both of which cost a
completed run its answer. **`recipes-mfi`:** `amplifier tool invoke` returned
the tool's result from a `try` whose `finally` awaited an *unbounded*
`session.cleanup()` — and `return result` does not reach the caller until that
`finally` completes, so a module whose cleanup blocks does not merely delay
teardown, it destroys the outcome (measured upstream: a completed recipe run,
then 28 minutes of silence, exiting instantly on SIGTERM). The result is now
emitted and flushed *before* cleanup is entered, and cleanup is bounded,
cancelled, and — if it ignores cancellation too — escaped from, saying so.
**`recipes-2v5`:** `~/.amplifier/settings.yaml` had one writer that truncated
in place *and* did an unlocked read-modify-write of the entire file, on the
startup path of every `amplifier` process. On a host running hundreds of them
that is both a torn-read window and a lost-update window; the observed symptom
was a registered `model_role_resolver` bundle vanishing for ~10 minutes and
coming back on its own. Every writer of shared state under `~/.amplifier/` now
goes through one atomic-replace helper, and the read-modify-write holds the
same lock `lib/settings.py` already used.

---

## 2. `recipes-mfi` — root cause and change

### Root cause

`amplifier_app_cli/commands/tool.py:235-242` (at `569c9b8`):

```python
result = await tool_instance.execute(tool_args)   # :235 — the run is DONE here
return result                                     # :239
finally:
    await session.cleanup()                       # :242 — unbounded
```

The only thing that prints is `tool_invoke` (`commands/tool.py:486-488`), and
it does not run until `asyncio.run(...)` returns — which does not happen until
the `finally` completes. The work succeeded, the outputs were on disk, and no
caller could ever learn it.

### Change

Both remedies the item named, because they guarantee different things — (1)
guarantees the answer, (2) guarantees the exit:

1. **Emit before cleanup.** `_invoke_tool_from_bundle_async` takes an
   `emit(ok, payload)` callback, invoked with the outcome — result *or*
   exception — before the `finally` is entered, and `_print_result` /
   `_print_error` flush explicitly (a `print()` into a pipe is block-buffered;
   an unflushed result is not an answer).
2. **Bound the cleanup** (`_cleanup_session_bounded`), as a ladder:
   `asyncio.wait({task}, timeout=…)` → WARNING + `task.cancel()` + grace →
   WARNING + flush + `os._exit`.

`asyncio.wait` deliberately, **not** `asyncio.wait_for`: `wait_for` cancels the
inner task and then waits for that cancellation to *complete*, so a cleanup
that ignores cancellation reintroduces the same unbounded hang. (This is the
one place the implementation departs from the GOAL's literal wording, and it
follows the item body's own "prefer `asyncio.wait`" note.) The third rung
exists because a task that ignores cancellation would wedge `asyncio.run()`'s
own shutdown, which cancel-and-gathers every remaining task — there is no
non-blocking way back to the caller from there. It is the documented last
resort and it names itself in the log.

The bound is configurable: `AMPLIFIER_TOOL_CLEANUP_TIMEOUT`, then
`tool.cleanup_timeout_seconds` in settings.yaml, then 30s. A non-numeric or
non-positive value is refused with a WARNING and the default used — "0 means
wait forever" is the behaviour this exists to remove.

`_get_mounted_tools_from_bundle_async` (`amplifier tool list`) got the same
bound; it had the identical shape.

### Measured

**Fail-before** (`evidence/mfi-fail-before.txt`) — `tool.py` reverted to
`569c9b8`, probe driving the real click command with a wedged cleanup:

```
$ timeout 25 uv run python docs/lanes/cli-mfi-2v5/evidence/probe_invoke.py
PROBE_EXIT=124        # killed at 25s. Zero output. The answer is gone.
```

**Pass-after** (`evidence/mfi-pass-after.txt`), same probe, fix applied:

```
--- returned after 2.01s, exit_code=0
{ "status": "success", "tool": "probe", "result": {...} }
Session cleanup exceeded 2s and was ABANDONED -- the tool's result was already
emitted, so the run's outcome is intact.
```

**Live, real CLI, real bundle** — the GOAL's manual gate
(`evidence/manual-tool-invoke.txt`):

```
$ uv run --project . amplifier tool invoke recipes -b anchors-amp-dev operation=list
Result from recipes:
  {'sessions': [], 'count': 0}
real 0m2.601s          # exit 0; the bound costs a healthy teardown nothing
```

And with the bound forced to fire (`evidence/abandon-stdout.txt`,
`evidence/abandon-stderr.txt`, `AMPLIFIER_TOOL_CLEANUP_TIMEOUT=0.001`) — note
the result JSON is on **stdout**, uncorrupted, and the warning on **stderr**:

```
stdout: { "status": "success", "tool": "recipes", "result": "{'sessions': [], 'count': 0}" }
stderr: Session cleanup exceeded 0.001s and was ABANDONED -- ...
exit 0
```

`tests/test_tool_invoke_cleanup_bound.py` (15 tests) pins all of it, including
a **real child process** that proves the `os._exit` rung actually ends a
process whose cleanup ignores cancellation.

---

## 3. `recipes-2v5` — root cause and change

### Root cause

Two mechanisms, not one. Atomicity alone would not have fixed the incident.

**(a) Torn reads.** `utils/settings_manager.py:59` opened
`~/.amplifier/settings.yaml` with `"w"` — truncate in place. A concurrently
starting session could read a partial file, and a partial YAML document parses
*fine*; it simply lacks whatever had not been written yet. That is exactly a
settings.yaml with no resolver-registering bundle in it.

**(b) Lost updates — the bigger one.** `save_update_last_check()` is a
read-modify-write of the **entire** file (`load_settings()` → mutate
`updates.last_check` → `save_settings()`), it held **no lock**, and it fires
from `utils/startup_checker.py:70` on the startup path of *every* `amplifier`
process. `lib/settings.py` (what `bundle add` uses) already took
`settings.yaml.lock` — but a lock only one side takes is not a lock. A
`bundle add` landing between this function's read and its write was silently
erased, which is precisely "the capability was present, then absent for ~10
minutes, then present again" with no command having changed.

A third, sharper edge in the same function: `load_settings()` swallowed a parse
error and returned `DEFAULT_SETTINGS`, which `save_settings()` then wrote
back — replacing a real settings file with a three-key stub.

### Every writer found, and what changed

New single home: `amplifier_app_cli/utils/atomic_write.py` —
`atomic_write_text` / `atomic_write_yaml` / `atomic_write_json`, all doing
temp-file-in-the-same-directory → `fsync` → `os.replace` (+ best-effort
directory fsync, skipped where unsupported).

| # | Writer | File written | Before | After |
|---|--------|--------------|--------|-------|
| 1 | `utils/settings_manager.py:59` `save_settings` | `~/.amplifier/settings.yaml` | truncate in place | `atomic_write_yaml` |
| 2 | `utils/settings_manager.py:35` `load_settings` (defaults) | `~/.amplifier/settings.yaml` | truncate in place, unlocked | atomic + locked, re-checks existence under the lock |
| 3 | `utils/settings_manager.py` `save_update_last_check` | `~/.amplifier/settings.yaml` | unlocked read-modify-write | whole RMW under `settings.yaml.lock`; refuses to rewrite an unparseable file |
| 4 | `lib/settings.py:1247` `_write_scope` (`bundle add/remove`, provider, source, routing, denied-dirs — all scopes) | `~/.amplifier/settings.yaml`, `.amplifier/settings.yaml`, `.amplifier/settings.local.yaml`, session settings | tmp+replace, **no fsync** | `atomic_write_yaml` (adds fsync; byte-identical output, `sort_keys=True` preserved) |
| 5 | `commands/routing.py:1043` `save_custom_matrix` | `~/.amplifier/routing/<name>.yaml` | truncate in place | `atomic_write_yaml` |
| 6 | `commands/routing.py:1938` (interactive `routing create`) | `~/.amplifier/routing/<name>.yaml` | truncate in place | `atomic_write_yaml` |
| 7 | `key_manager.py:114` `save_key` | `~/.amplifier/keys.env` | tmp+replace, no fsync | `atomic_write_text` |
| 8 | `utils/update_check.py:112` `_mark_checked` | `~/.amplifier/.last_update_check` | truncate in place | `atomic_write_text` |
| 9 | `utils/update_check.py:127` `_save_cached_result` | `~/.amplifier/.update_cache.json` | truncate in place | `atomic_write_json` |
| 10 | `utils/update_executor.py:531` | `install-state.json` | truncate in place | `atomic_write_json` |
| 11 | `lib/sources_compat.py:298` | `<cache>/.amplifier_cache_metadata.json` | truncate in place | `atomic_write_json` |
| 12 | `commands/update.py:1123` | `<cache>/.amplifier_cache_meta.json` | truncate in place | `atomic_write_json` |

Rows 1–4 are the item's subject (settings.yaml). 5–6 are the other *registry*
YAML the CLI rewrites, read by hooks-routing at session start. 7–12 are the
remaining shared state under `~/.amplifier/` with the identical failure shape,
several of them on the same every-process startup path; they were one line each
and leaving them would have made "every writer" untrue.

**Deliberately not changed:** `session_store.py` writes (transcript, metadata,
config) go through `amplifier_foundation.write_with_backup` — a different repo,
and per-session directories are not shared state. `main.py:1305` writes a
per-session transcript file, same reasoning.

**Windows — found by CI, then fixed properly.** On Windows `os.replace`
(`MoveFileEx`) fails with `PermissionError` while another process holds the
*destination* open, even for reading. POSIX never raises this here. The
race test hit it immediately on `windows-latest` (both 3.11 and 3.12) — and
that is a real production hazard, not a test artifact: losing a `bundle add`
because another session happened to be reading settings.yaml would be its own
version of this bug. `_replace_with_retry` now retries the rename with
exponential backoff for up to `REPLACE_RETRY_SECONDS` (5s) and then **raises**,
with a WARNING naming the cause. The write is never silently dropped. Two
tests cover it: the retry succeeds after transient denials, and a permanently
blocked destination raises while leaving both the original file and the
directory clean.

### Measured

**Fail-before** (`evidence/2v5-fail-before.txt`) — `settings_manager.py` alone
reverted to `569c9b8`:

```
4 failed, 2 passed, 6 deselected
FAILED ...::test_timestamp_write_refuses_to_overwrite_an_unparseable_settings_file
FAILED ...::test_timestamp_write_is_atomic
FAILED ...::test_timestamp_write_blocks_on_the_same_lock_lib_settings_uses
FAILED ...::test_load_settings_creates_defaults_atomically
```

`tests/test_settings_atomic_write.py` (14 tests) covers the shape, the race,
and the Windows rename hazard:

* **shape** — the destination still holds the **old** bytes at the instant
  `os.replace` is called, the temp file is in the same directory, and it was
  `fsync`ed. Applied to `atomic_write_text`, to `save_update_last_check`, and
  to `AppSettings._write_scope`.
* **race** — three reader threads polling through 80 rewrites alternating two
  large, different-length documents: every observation must be one complete
  document. (Asserted only for the atomic writer — a "prove the
  old code tears" control would be timing-dependent, and a flaky test is worse
  than no control. The fail-before run above is the control.)
* **lost update** — holding `settings.yaml.lock` (the same lock file
  `lib/settings.py::_scope_lock` takes for the global scope) must stop
  `save_update_last_check` from changing anything at all, and after release the
  bundle registration written meanwhile must survive alongside the new
  timestamp.

---

## 4. Gates

| Gate | Result |
|------|--------|
| `uv run pytest -q` | **1836 passed, 1 skipped, 13 deselected, 1 xfailed** (`evidence/full-suite.txt`) |
| `uv run pytest -m integration -q` | **13 passed** (`evidence/integration-suite.txt`) |
| `ruff check` on every touched file | clean (repo has 16 pre-existing findings in untouched files; ruff is not in CI) |
| Manual: `amplifier tool invoke recipes -b anchors-amp-dev operation=list` | prints result, exit 0, 2.6s (`evidence/manual-tool-invoke.txt`) |
| Full CI matrix (ubuntu/macos/windows × py3.11/3.12) | first push: 7/9 green, **windows 3.11 + 3.12 failed** on the `os.replace`-while-held-open behaviour above; fixed in the follow-up commit and re-run — see PR #314 checks |

One transient: the very first full-suite run failed
`test_truststore_wrap_bio_shim.py::test_real_truststore_is_covered_at_cli_import`
with `truststore is not importable`. It reproduced on a **stashed (clean
`569c9b8`)** tree too and disappeared once `uv run` finished re-syncing the
venv — an environment-sync artifact, not this change. The final runs recorded
above are green.

---

## 5. Still open / notes for the manager

* `work_claim` refuses to hold two items at once ("already holding
  'recipes-mfi' … resolve it before claiming another"), so the two items were
  worked under one lane but claimed serially. Both are resolved against this
  one PR.
* The `os._exit` rung has never fired in a real run here — only in the child
  process test, where cleanup was written to ignore cancellation on purpose.
  Real teardowns observed so far honour cancellation and stop at rung 2.
* Abandoning a cleanup emits a `RuntimeWarning: coroutine ... was never
  awaited` from the garbage collector on the way out. That is inherent to
  abandoning and only appears when the bound actually fires; it is left visible
  rather than suppressed, because it is true.
