# DONE-NOTE — lane 9w0 · `model_performance-9w0`

**W3-PREREQ (2 of 2, PRODUCER): app-cli exposes a timed-out sub-session's partial output**

| | |
|---|---|
| Repo | `microsoft/amplifier-app-cli` |
| Branch | `lane/9w0-delegate-timeout-partial-producer` |
| Draft PR | https://github.com/microsoft/amplifier-app-cli/pull/297 |
| Parent commit | `ab47608fc989e13f9674c3f5f9efc5625a9d7673` |
| Consumer half it pairs with | amplifier-foundation `f42f48c` (`model_performance-bp0`, PR #353) — **merged** |
| Spend authority | **$0** (pure code change, no API/DTU spend authorized) |
| **Spend incurred** | **$0.00.** No API calls, no DTU, no infrastructure created, nothing to tear down. Local `pytest` + local reads only. The authority was sized for a code lane and it closed: every deliverable below landed inside it. |
| Outcome | **A — RESOLVED.** All deliverables DONE; none NOT-POSSIBLE. |

---

## 1. The finding that mattered: the designed patch does not work against the shipped consumer

37n's `PATCH-app-cli-session-spawner.diff` publishes the partial from an
`except BaseException:` handler around `child_session.execute()` — i.e. from the
**child's own unwind**. Applied faithfully against the **real merged consumer**,
that shape still reports `partial_available: false`, and it does so **silently**:
both halves' unit tests pass.

**Root cause (measured, not inferred).** `tool-delegate`'s
`_await_child_with_deadline` (foundation `f42f48c`, `__init__.py:707-736`)
deliberately does **not** wait for a child that is slow to unwind. At the
deadline it calls `child_task.cancel()`, **detaches** the task, and raises
`_DelegateTimeoutExpired` immediately. The `except _DelegateTimeoutExpired:`
handler then calls `session.partial` straight away — before the cancelled child
task has been scheduled to run any exception handler of its own.

Observed ordering in a single run (evidence `04`):

```
WARNING amplifier_module_tool_delegate  Agent 'explorer' timed out after 1s … No partial output could be recovered.
WARNING amplifier_app_cli.session_spawner  Sub-session …_explorer did not complete; preserved 2 partial text segment(s), 42 chars
```

The producer preserved the work — one line **after** the consumer had already
given up on it and reported `false`.

37n was not wrong at the time: DESIGN.md was written against `asyncio.timeout`
semantics (`:1092`, "`async with asyncio.timeout(self.timeout)`"), which *does*
wait for the child to unwind. The consumer that actually shipped replaced that
with cancel-and-detach, and no unit test on either side could see the change.

**The fix (this lane's only design deviation).** The accumulator record is
entered in the registry when it is installed and updated **in place** as text
arrives — readable at *any* instant, with no dependence on cancellation ordering
— and is removed when the sub-session completes normally. Nothing else about
37n's design changed: same capability name, same payload shape, same destructive
read, same 64-session cap, same synchronous-only cancellation path.

This is exactly the drift the cross-repo round trip exists to catch, and it was
invisible to every test either repo owns on its own.

---

## 2. Deliverables

| # | Deliverable | State | Evidence |
|---|---|---|---|
| 1 | Partial retrievable through `get_partial_output` after a per-delegate timeout | **DONE** | `evidence/03` (fail-before), `evidence/07` (pass-after), `tests/test_session_spawner_partial.py` (12 tests) |
| 2 | **Cross-repo contract**: real app reader ↔ real foundation consumer, overlaid copies, against foundation `f42f48c` | **DONE** | `evidence/07` — 2 passed |
| 3 | `partial_available: true` actually reached (k64 gate **G-D4**) | **DONE** | `evidence/06` — side-by-side parent (`false`) vs patched (`true`, 42 chars, 2 segments, `source: spawn-accumulator`) |
| 4 | Normal completions unchanged — **byte-identical**, shown not asserted | **DONE** | `evidence/05` — `diff` empty, identical sha256 `ef8c86fd…` |
| 5 | The two `TestSpawnEnrichment` failures shown on the parent | **DONE, with a correction — see §4** | `evidence/00` |
| 6 | Fail-before evidence committed and pasted in the PR body | **DONE** | `evidence/03`, `evidence/04` |
| 7 | Draft PR on origin naming foundation `f42f48c` as the consumer half | **DONE** | [PR #297](https://github.com/microsoft/amplifier-app-cli/pull/297), draft; `publication` block in `DONE.json` read back from the remote |
| 8 | This DONE-NOTE under the lane artifact root | **DONE** | this file |

Nothing was dropped, and no deliverable was cap-bound.

---

## 3. What changed

`amplifier_app_cli/session_spawner.py`

* `_PARTIAL_OUTPUTS` registry (cap 64, oldest-first eviction, eviction logged).
* `get_partial_output(sub_session_id)` — the `session.partial` capability.
  Destructive read; a record with no text reads as `None`.
* `_open_partial` / `_publish_partial` / `_discard_partial` / `_seal_partial`.
* `content_block:end` accumulator registered at priority 999 on both the spawn
  and the resume path; `session.partial` registered on every child coordinator
  so a *grandchild* that times out is recoverable too.
* `except BaseException:` → `_seal_partial` (confirm + log; synchronous only),
  `else:` → `_discard_partial` on normal completion.

`amplifier_app_cli/session_runner.py`

* `session.partial` registered on the root session in `register_session_spawning`.

`tests/test_session_spawner_partial.py` — 12 tests, including
`test_partial_is_readable_before_the_child_has_unwound`, which pins the ordering
property **without** needing foundation on the path. That test is the in-repo
guard against silently regressing back into 37n's shape.

`docs/lanes/9w0-delegate-timeout-partial-producer/` — round-trip test, probe,
evidence, this note.

**Not touched:** amplifier-foundation, or any repo other than this one.
`settings.timeout` still defaults to `None`; k64's eval was not run. Both are
separately funded.

---

## 4. Deviations from the goal text — stated, not absorbed

**(a) The two "pre-existing failures" do not exist on this parent.** The goal
requires them to be shown failing on the parent commit, "patched and unpatched
alike". They do **not** fail:
`tests/test_session_spawner.py::TestSpawnEnrichment::test_spawn_result_includes_status_and_turn_count`
and its `test_resume_…` twin both **PASS** at `ab47608`, and the named-suite
baseline is **73 passed, 0 failed** — not 37n's "71 passed, 2 failed" at
`f16375fc`. The baseline improved between the two commits. Reported rather than
manufactured; the instruction's *purpose* (do not report someone else's failure
as yours) is satisfied — there is no failure to attribute in either direction.

**(b) The patch was re-targeted by hand, as the goal predicted.**
`git apply --check` exits 1 at `ab47608` (`evidence/02`). The regions had moved
under the mid-run transcript-checkpoint work (`_install_transcript_checkpoint`,
`unregister_checkpoint`), which also *changed* the comment the patch edits:
main said "there is deliberately NO `except` here". That comment is now accurate
again, and says why the `except` that exists is safe.

**(c) One design change, described in §1.** Eager publication instead of
publish-on-unwind. Everything else follows 37n's design. The counter-evidence
run (`evidence/04`) keeps the alternative honest: 37n's exact shape, everything
else identical, still `false`.

**(d) `partial_max_chars` truncation is the consumer's job**, not this half's.
The producer hands over the full accumulated text; foundation caps it at 20 000
chars keeping the tail. Confirmed reading `_collect_partial`, and pinned by
`partial_truncated: false` in `evidence/06`.

---

## 5. Known limits (honest, not smuggled)

* **Subprocess children.** `spawn_sub_session(use_subprocess=True)` runs the
  child in another process, so the in-memory registry cannot see its text. Such
  a timeout degrades to `partial_available: false` — the same graceful
  degradation as no capability at all, never an error. Not in scope here.
* **Text blocks only.** The accumulator keys on
  `block["type"] == "text"`. Thinking blocks and tool-call blocks are not
  preserved. `segments` counts text segments, not turns — named for what it is.
* **No provider was driven.** Every check here is local: mocked child sessions,
  the real spawner, the real delegate tool. `$0` authority permits nothing else,
  and nothing else was needed for this contract.
* **Registry cap under extreme fan-out.** >64 concurrent live sub-sessions per
  root process would evict the oldest live record. Measured fan-out is P50 2 /
  P95 6 / max 7 (37n DESIGN.md §6), so this does not bind today; the eviction is
  logged rather than silent.

---

## 6. Reproduce

```bash
# in-repo suite (12 new tests, full suite green)
.venv/bin/python -m pytest tests/ -q                      # 1659 passed, 1 skipped, 1 xfailed

# cross-repo round trip — overlaid COPIES, neither repo mutated
git clone --depth 5 https://github.com/microsoft/amplifier-foundation /tmp/f   # f42f48c
cp -rL /tmp/f/modules/tool-delegate/amplifier_module_tool_delegate /tmp/td/
cp -rL ./amplifier_app_cli /tmp/itest/
PYTHONPATH=/tmp/td:/tmp/itest .venv/bin/python -m pytest \
    docs/lanes/9w0-delegate-timeout-partial-producer/test_partial_roundtrip.py -q   # 2 passed

# G-D4 side by side
PYTHONPATH=/tmp/td:/tmp/itest .venv/bin/python \
    docs/lanes/9w0-delegate-timeout-partial-producer/probe_partial_roundtrip.py timeout
```

`conftest.py` for the out-of-repo runs supplies only an `anyio_backend`
fixture returning `"asyncio"`.
