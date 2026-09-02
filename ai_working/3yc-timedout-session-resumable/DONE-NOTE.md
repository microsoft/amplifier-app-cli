# DONE-NOTE — `model_performance-3yc`

**A timed-out sub-session is never persisted, so the session_id in its result is not resumable**

**Lane spend: $0.00** — code reading + local `pytest` only. No API calls, no DTU, no infrastructure
created (nothing to register in the infra ledger, nothing to tear down).
**Repo:** `amplifier-app-cli` · branch `lane/3yc-timedout-session-not-resumable` · base `963d793`
**Claim tags:** (knob) · (family) · (confidence: measured / inferred / assumed) · (evidence: file:line)

---

## 1. VERIFIED MECHANISM (against the current base, not the filing base)

The item was filed against `f16375fc`. Re-read at **`963d793`** (current `origin/main`); the mechanism
is unchanged, only the line numbers moved.

| claim | evidence at `963d793` | confidence |
|---|---|---|
| the child transcript is read only after a successful `execute()` | `session_spawner.py:845` `response = await child_session.execute(instruction)` → `:857` `transcript = await context.get_messages()` | measured |
| `store.save` runs only after that | `session_spawner.py:887` `store.save(sub_session_id, transcript, metadata)` | measured |
| there is no `except` on the execute block — only `finally` | `session_spawner.py:843-910` | measured |
| the same defect exists on the RESUME path (not mentioned in the item) | `session_spawner.py:1619` execute → `:1629` `store.save`, same `try/finally` shape | measured |
| the metadata `store.save` needs is fully known BEFORE execute | `merged_config` `:298`, `agent_config` `:290/:295`, `self_delegation_depth` (parameter `:242`), `_extract_bundle_context(parent_session)` — none depend on the response | measured |
| `store.save` itself is fully synchronous | `session_store.py:100-131` — `_save_transcript` / `_save_metadata` / `write_with_backup`, no `await` anywhere | measured |
| resume reconstructs purely from `metadata["config"]` + `metadata["agent_overlay"]` + transcript | `session_spawner.py:966-970`, `:1314-1327`, `:1556-1559` | measured |

The last two are the load-bearing findings. Together they mean (a) the persist step needs **exactly one
await** — `context.get_messages()` — and (b) everything else it needs is available before the run starts.

---

## 2. THE DECISION — option (b), persist during the run

**Chosen: (b) persist the transcript incrementally during the run, so no cancellation-path write is
needed at all.** The cancellation path gains **no new code, no new await, and no new write**.

### Why not (a) — `asyncio.shield` + a hard secondary timeout

Rejected on a measured, not aesthetic, ground: **the bound would be partly fictional.**

1. `store.save()` is **synchronous** (`session_store.py:100-131`). `asyncio.wait_for` can only
   interrupt at an `await`. It therefore cannot bound the disk write — the part most likely to be
   slow on a loaded or networked filesystem. The "hard secondary timeout" would bound
   `get_messages()` and nothing else, while reading as though it bounded the save.
2. Once a deadline's `CancelledError` has been delivered and caught, a **fresh await is not
   re-cancelled** — it simply blocks. This is demonstrated directly, as executable code, in
   `test_the_probe_would_catch_a_violating_implementation`: the option-(a) shape hangs past its own
   0.2 s deadline and never unwinds. That is precisely the hang the timeout exists to bound.
   **(confidence: measured — the control test hangs deterministically.)**
3. `shield` + `wait_for` leaks the shielded task when the outer `wait_for` fires, and a re-entrant
   parent cancellation (Ctrl-C, an outer timeout) then propagates out of the handler — which
   `DESIGN.md §3` establishes would destroy the sibling delegates the timeout path exists to protect.

### Why not (c) — mark the result explicitly non-resumable

Legitimate, and it was seriously weighed. Rejected for three reasons, in order of weight:

1. **The value discarded is large and known.** Measured delegate legs run **284–1543 s** ([SOL], via
   `00 §2c`); sol S1 runs cost **$15.96–$27.18 median/run** (`00 §2c`). Option (c) makes every timeout
   throw away the whole transcript and forces the caller to pay for it again from zero. Option (b)
   costs a handful of small synchronous writes per run.
2. **It does not compose with 37n.** `model_performance-37n` already preserves the in-flight
   assistant *text* via the `session.partial` capability. What is still missing is the *completed
   turns*, which is exactly what makes "resume where it left off" work. (b) supplies the missing
   half; (c) declares the gap permanent.
3. **Its deliverable is not in this repo.** The result shape (`partial_available`, `guidance`,
   `status`) lives in `amplifier-foundation`'s `tool-delegate`, outside this lane's owned repo. A
   `resumable: false` flag shipped here could not be surfaced by the consumer without a second,
   coordinated PR. (This is a practical constraint, not the reason — reasons 1 and 2 stand alone.)

**Honest note:** (c) is still the correct *labelling* answer for the residual gap. The subprocess
spawn path (`session_spawner.py:637`) returns before any checkpointing and remains unresumable on
timeout — see §5.

### The design, and why `provider:request`

* The transcript is checkpointed **during normal execution**, where blocking is already accepted
  (the post-run save has always done exactly this work — this moves *when*, not *what*).
* One checkpoint is written **before `execute()`** (pre-registration), so the advertised `session_id`
  resolves in `SessionStore` even if the timeout fires during the very first LLM call.
* Mid-run checkpoints fire on **`provider:request`** — emitted at
  `loop_streaming/__init__.py:3202` (per iteration) and `:2996` (turn start).
  **(confidence: measured — code read at cache `amplifier-module-loop-streaming-b0b975ea6a1072dd`.)**

  `provider:request` is not an arbitrary choice: it is **the only point in the loop where the message
  list is guaranteed tool-pair-balanced.** The previous round's tool results have all been appended,
  and the next assistant message (which may open new `tool_calls`) does not exist yet. Checkpointing
  on `provider:response` would persist an assistant message with unmatched `tool_calls`, and resuming
  that transcript reproduces the `InvalidRequestError: No tool call found for function call output`
  class of failure recorded for `context-managed` in `00 §2g` (29 of 30 turns).
  **(confidence: inferred — the balance property is read from the loop; the resulting provider error
  is measured, but in the cited `context-managed` runs, not here.)**
* Checkpoints are labelled `status: "in_progress"`; only the post-run save writes `status: "complete"`.
  A caller can therefore tell a rescued checkpoint from a finished session.
* Applied to **both** `spawn_sub_session` and `resume_sub_session` — the resume path had the same
  defect and the item did not mention it.

### The knob

`AMPLIFIER_SPAWN_CHECKPOINT_INTERVAL_S`, default **30.0 s**. **CHOSEN, NOT MEASURED, and labelled as
such in the source comment.** No data exists on sub-session checkpoint sizes because no mid-run
checkpoint has ever been written, so there is nothing to bank (`00 §5` rules 3 and 6). The reasoning
recorded in-source: 30 s bounds the transcript lost to a timeout to at most one window against legs
of 284–1543 s, while capping write amplification on fast-iterating sub-sessions. A negative value
disables checkpointing entirely and restores the pre-fix behaviour exactly — pinned by a test.

---

## 3. THE HARD INVARIANT, AND HOW IT IS PROVED

> The fix must NOT introduce an unbounded await on the cancellation path.

Proved by `TestNoUnboundedAwaitOnCancellationPath` — two assertions plus a control:

1. **`test_hanging_get_messages_does_not_delay_the_unwind`** — `context.get_messages()` is made to
   hang **forever** from the instant cancellation is delivered. The spawn runs under a 0.2 s
   `asyncio.timeout`, exactly as `tool-delegate:1092` does. Asserts: the unwind completes (the
   harness fails loudly at 5 s rather than hanging the suite), elapsed < 2 s, **and**
   `get_messages`'s call count did not move after cancellation began. The count assertion is what
   stops the test passing by accident.

2. **`test_the_probe_would_catch_a_violating_implementation`** — the **inverted control**, present
   because of `00 §5` rule 5. *This is a gate that would otherwise be vacuous:* the unpatched code
   ALSO has no await on its cancellation path, so assertion 1 passes before the fix as well
   (verified — see §4). It is a regression guard, not a defect reproduction, and a guard is worth
   nothing unless it can fail. The control reproduces the rejected option-(a) shape and asserts the
   probe's instrument **does** catch it. **Disclosed rather than presented as a pass.**

3. **`test_cleanup_still_runs_and_the_timeout_still_propagates`** — the timeout still surfaces as
   `TimeoutError`, `child_session.cleanup()` still runs, `unregister_child` still runs, and the
   checkpoint hook is unregistered even on the timeout path.

### The contract the acceptance names

`TestTimedOutSessionIsResumable` — two tests:

* `test_timed_out_spawn_leaves_a_loadable_session` — after a real 0.2 s timeout, `SessionStore` holds
  the session, the transcript is the preserved messages, `status == "in_progress"`, and
  `metadata["config"]` is present.
* `test_timed_out_session_round_trips_through_resume` — the **full recovery move, end to end**: spawn
  → time out → call the real `resume_sub_session(session_id)` → assert the preserved transcript is
  restored into the resumed session's context. This is the acceptance criterion's first branch
  ("the advertised session_id genuinely resumes and returns the preserved transcript"), executed.

---

## 4. VERIFICATION

| check | result |
|---|---|
| full suite, baseline at `963d793` before any change | **1560 passed, 1 skipped, 13 deselected, 1 xfailed** |
| full suite, patched (`-p no:randomly`) | **1571 passed, 1 skipped, 13 deselected, 1 xfailed** |
| full suite, patched (default random order) | **1571 passed, 1 skipped, 13 deselected, 1 xfailed** |
| new test file alone | **11 passed** |
| new test file against the UNPATCHED spawner (falsifier check) | **8 failed, 2 passed** |
| `ruff check` / `ruff format --check` on every file this PR touches | clean |

**The falsifier check matters, and its two passes are disclosed, not hidden.** Reverting only
`session_spawner.py` and re-running the new file gives 8 failures. The two that still pass are
`test_hanging_get_messages_does_not_delay_the_unwind` (correct — the unpatched cancellation path is
also await-free; this is the regression guard discussed in §3.2) and
`test_negative_interval_disables_checkpointing_entirely` (correct — it pins that the escape hatch
reproduces pre-fix behaviour). Neither is a defect reproduction and neither is claimed as one.

**One existing test was modified**, and the reason is not "to make the new code pass": `FakeHooks` in
`tests/test_session_spawner.py` (two copies) modelled the hook registry as a **single handler slot,
ignoring the event name**. The real registry is event-keyed. Registering a second hook made the last
writer win, hiding the `orchestrator:complete` handler the test asserts on. The fake now honours the
event name. The test's intent and assertions are untouched.

---

## 5. WHAT THIS DOES NOT CLAIM

1. **No eval was run. $0 lane.** Every result above is a local unit test. No live delegate has ever
   timed out under this patch.
2. **The 30 s default is chosen, not measured** (`00 §5` rule 6). It is labelled as such in the source.
3. **Write amplification is not measured.** `_save_transcript` rewrites the whole JSONL per
   checkpoint, so cost is O(checkpoints × transcript size), bounded by the throttle. No workload
   measurement exists. `store.save` is synchronous, so each checkpoint briefly blocks the event loop
   — the same operation the post-run save has always performed, now up to N times per run.
4. **The subprocess spawn path is NOT covered.** `session_spawner.py:637` returns before any
   checkpointing, so a subprocess-mode delegate that times out is still unresumable. Not in scope
   here; this is where option (c)'s explicit "not resumable" labelling is still the right answer.
5. **Up to one throttle window of transcript can still be lost.** Because the transcript only changes
   between provider calls, the practical loss is "iterations that completed within the last 30 s",
   not 30 s of work — but that is reasoned, not measured.
6. **The tool-pair-balance argument for `provider:request` is inferred** from reading the loop. The
   consequence of getting it wrong is measured, but in `00 §2g`'s `context-managed` runs, not here.
7. **This PR does not enable any timeout.** `settings.timeout` remains `None` by default in
   `tool-delegate`. Landing order from `DESIGN.md §7` is unchanged: foundation, then app-cli, then
   sweep the timeout.
8. **No Anthropic guardrail run was performed** (`00 §5` rule 9). This patch changes only local disk
   writes on the app side — it adds no request, alters no prompt, and touches no provider payload, so
   there is no cache surface to regress. That is an argument, not a measurement.

---

## 6. FILES

| file | what |
|---|---|
| `amplifier_app_cli/session_spawner.py` | `_checkpoint_interval_s`, `_write_checkpoint`, `_install_transcript_checkpoint`; metadata + store construction moved before `execute()`; checkpoint wired into both the spawn and resume paths; `status` field on saved metadata |
| `tests/test_timedout_session_resumable.py` | 11 new tests — the contract, the invariant, the inverted control, the boundary choice, best-effort behaviour, the throttle and the escape hatch |
| `tests/test_session_spawner.py` | `FakeHooks` made event-keyed (see §4) |
| `ai_working/3yc-timedout-session-resumable/DONE-NOTE.md` | this note |
