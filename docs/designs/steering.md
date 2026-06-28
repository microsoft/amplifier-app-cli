# Design: Mid-Turn Steering for the Streaming Orchestrator (Thin Slice)

Status: Ready for implementation
Scope: Two repos, branch `feat/steering` in both.
- Orchestrator: `amplifier-module-loop-streaming/amplifier_module_loop_streaming/__init__.py` (`StreamingOrchestrator`)
- App-CLI: `amplifier-app-cli/amplifier_app_cli/` (`main.py`, `approval_provider.py`, `session_runner.py`)

> This spec is complete enough to build from without further design decisions.
> Line numbers are anchors-at-time-of-writing; the implementer MUST re-confirm by
> reading the named function and the quoted nearby code, because line numbers drift.

---

## 1. Outcome (what "done" means)

A user types a message while the agent is actively working a turn. The message is
queued and injected as a **user-role** message at the next safe boundary — the
top-of-iteration point that sits *after the current tool round completes and before
the next provider call*. The model demonstrably acts on it: the
`orchestrator:steering_injected` event appears in `events.jsonl` AND the agent's
subsequent tool calls / final answer visibly change to follow the redirect.

Default semantics: **act after the next tool call completes.** Success is "the agent
acts on the redirect," not "a message arrived."

This is the THIN slice: **ONE queue, ONE drain semantics (mid-turn steer).** No
FollowUpQueue, no drop-a-queued-steer, no child-target signaling.

---

## 2. The contract between the two repos (the only coupling)

The orchestrator and the app are bricks connected by exactly ONE stud: a coordinator
**capability** named `session.steer`. The orchestrator owns the queue and registers
the capability; the app discovers and calls it. Neither imports the other.

### Capability: `session.steer`

| Field | Value |
|---|---|
| Name | `"session.steer"` |
| Registered by | `StreamingOrchestrator` (orchestrator repo) in `mount()` |
| Registered via | `coordinator.register_capability("session.steer", orchestrator.steer)` |
| Value | bound method `steer(message: str) -> None` |
| Consumed by | app-cli stdin reader via `coordinator.get_capability("session.steer")` |
| Semantics | Non-blocking enqueue. The message is injected as a user-role message at the next top-of-iteration drain. |
| Raises | `ValueError` if `message` is empty / whitespace-only. `SteeringQueueFull` (subclass of `RuntimeError`) if the bounded queue is full. |
| Absence | `get_capability("session.steer")` returns `None` when the mounted orchestrator does not support steering → app must fail loud to the user, never silently drop typed lines. |

This mirrors the existing `session.spawn` precedent
(`session_runner.py:520`, `register_session_spawning`) and the capability API used
throughout the codebase: `coordinator.register_capability(name, value)` /
`coordinator.get_capability(name)`.

---

## 3. Orchestrator changes (`amplifier-module-loop-streaming`)

### 3.1 New file: `amplifier_module_loop_streaming/steering.py`

Self-contained copy of the proven pattern (do NOT import from the attractor bundle —
it is not a runtime dependency). Adds a bound + validating queue.

```python
"""Bounded, fail-loud steering queue for the streaming orchestrator.

Mid-turn steering: the host enqueues user messages while a turn is running;
the orchestrator drains them at the top-of-iteration boundary (after the prior
tool round, before the next provider call) and injects each as a user-role
message. FIFO. Fail loud — never silently drop.
"""

from __future__ import annotations

import asyncio


class SteeringQueueFull(RuntimeError):
    """Raised when steer() is called on a full bounded queue (fail loud)."""


class SteeringQueue:
    """FIFO queue for mid-turn steering messages.

    Bounded to surface misuse loudly rather than grow without limit.
    """

    DEFAULT_MAXSIZE = 100

    def __init__(self, maxsize: int = DEFAULT_MAXSIZE) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=maxsize)

    def steer(self, message: str) -> None:
        """Non-blocking enqueue of one steering message.

        Raises ValueError on empty/whitespace-only input, SteeringQueueFull
        when the bound is reached. Never blocks, never silently drops.
        """
        if message is None or not message.strip():
            raise ValueError("steering message must be non-empty")
        try:
            self._queue.put_nowait(message)
        except asyncio.QueueFull as exc:
            raise SteeringQueueFull(
                "steering queue is full; message rejected"
            ) from exc

    def drain(self) -> list[str]:
        """Dequeue all pending messages in FIFO order (possibly empty)."""
        messages: list[str] = []
        while not self._queue.empty():
            try:
                messages.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return messages

    @property
    def is_empty(self) -> bool:
        return self._queue.empty()
```

**Decisions, justified:**
- **Bound = 100.** Far beyond any human typing rate within a single turn; reaching it
  signals a programming error (e.g. an app loop spamming `steer()`), so we fail loud
  rather than mask it. The number is a named constant for easy tuning.
- **Overflow = reject (raise `SteeringQueueFull`).** No silent drop. The app surfaces
  the rejection to the user.
- **Validation rejects empty/whitespace loudly** (`ValueError`) so a stray newline can
  never become an empty user turn.

### 3.2 `StreamingOrchestrator.__init__` — own the queue

Anchor: the block initialising instance state, near
`self._pending_ephemeral_injections: list[dict[str, Any]] = []`
(`__init__.py:80`). Add:

```python
from .steering import SteeringQueue  # top-of-file import

# ... in __init__, alongside the other state fields:
self._steering_queue = SteeringQueue()
```

The orchestrator instance is mounted once per session and persists across turns, so
the queue persists across turns too (relevant only to the last-drain edge — see 3.6).

### 3.3 Public `steer()` method

Add a method on `StreamingOrchestrator`:

```python
def steer(self, message: str) -> None:
    """Queue a steering message for injection at the next iteration boundary.

    Non-blocking. Raises ValueError (empty/whitespace) or SteeringQueueFull.
    This is the target of the `session.steer` coordinator capability.
    """
    self._steering_queue.steer(message)
```

No logging-and-swallow: validation/overflow errors propagate to the caller (the app),
which is responsible for surfacing them. Fail loud.

### 3.4 Drain helper

Add an async helper on `StreamingOrchestrator`:

```python
async def _drain_steering(self, context, hooks, iteration: int) -> int:
    """Drain queued steering messages into context as user-role messages.

    FIFO. Each message is appended via context.add_message({"role":"user",...})
    so the very next get_messages_for_request() picks it up, and an
    orchestrator:steering_injected event is emitted per message. Returns the
    number of messages injected (0 = no-op, no events, streaming undisturbed).
    """
    messages = self._steering_queue.drain()
    if not messages:
        return 0
    total = len(messages)
    for idx, msg in enumerate(messages):
        await context.add_message({"role": "user", "content": msg})
        await hooks.emit(
            "orchestrator:steering_injected",
            {
                "orchestrator": "loop-streaming",
                "content": msg,
                "iteration": iteration,
                "queued_remaining": total - idx - 1,
                "metadata": None,
            },
        )
    return total
```

Notes:
- Injecting a `role:"user"` message after `role:"tool"` results is consistent with this
  file's existing behavior — the ephemeral-injection paths already append user/system
  messages mid-stream after tool results (`__init__.py:314-320`), and the reference
  loop-agent does the same (SteeringTurn → user message after ToolResultsTurn). The
  provider adapters tolerate it.
- `metadata: None` is the standard empty extensibility slot for a stable event payload.

### 3.5 Primary drain point — top-of-iteration

Target: `_execute_stream()`, inside `while self.max_iterations == -1 or iteration < self.max_iterations:`.

Anchors:
- Cancellation check block at the top of the loop ends with `return` (`__init__.py:234-260`).
- `iteration += 1` (`__init__.py:262`).
- `PROVIDER_REQUEST` emit (`__init__.py:265`).
- `message_dicts = await context.get_messages_for_request(provider=provider)` (`__init__.py:278`).

**Insertion:** immediately after `iteration += 1` and before the `PROVIDER_REQUEST`
emit:

```python
iteration += 1

# Mid-turn steering: drain queued user messages BEFORE building the request,
# so they are part of this iteration's provider call. At iteration 1 this is
# "before the first LLM call"; at iteration N>1 this is "after the prior tool
# round, before the next provider call" — the single natural boundary.
await self._drain_steering(context, hooks, iteration)
```

This single point serves BOTH the "before first LLM call" case and the "after each
tool round" case, because the loop's top-of-iteration is exactly the post-tool /
pre-provider boundary. It sits *before* `get_messages_for_request()`, so injected
messages are guaranteed to be in the very next request. It is *before* the
branch split (streaming vs non-streaming at `__init__.py:391`), so it is
branch-independent.

**Streaming is undisturbed:** the drain runs before any token is produced, and is a
no-op (`return 0`, no events, no messages) when the queue is empty — the common case.
It never touches `_stream_from_provider` or `_tokenize_stream`.

### 3.6 Last-drain edge — decision: drain-and-revive

The loop ends via `break` at two places, both *after* the model produced no tool calls
for the turn:
- **Non-streaming branch:** the `if not tool_calls:` block streams the final text, does
  `await context.add_message(assistant_msg)`, then `break` (`__init__.py:582`).
- **Streaming branch:** `else: # No more tools, we're done` → `break` (`__init__.py:416-418`).

A steer enqueued *during that final provider call* arrives after the last
top-of-iteration drain and would be stranded if we simply broke.

**Decision: revive — do not end a turn while a steer is visibly queued.** Replace each
`break` with:

```python
# Last-drain edge: if a steer arrived during the final generation, don't end
# the turn — loop once more so the model acts on it this turn. The top-of-
# iteration drain (3.5) performs the actual injection.
if not self._steering_queue.is_empty:
    continue
break
```

**Why revive (not "leave queued"):**
- The GOAL is "the agent demonstrably acts on it." Leaving the message queued would
  defer it to the *next* `execute()` call, where it would be injected *after* the next
  user prompt (the new prompt is appended at `__init__.py:215` before the loop starts) —
  wrong order, and no action until the user types again. That silently defers and
  reorders the redirect.
- Revive guarantees same-turn action with minimal code: a single non-empty check at the
  two exit points, with injection still living in ONE place (the top-of-iteration drain).
- It is bounded: `continue` re-enters the `while`, which still honors `max_iterations`.
  No infinite loop — `drain()` empties the queue, so a fresh provider call happens only
  while new steers keep arriving (which is the user's intent).
- The user may have already seen the "final" streamed text; the revived iteration then
  produces a follow-up response addressing the steer. That is exactly the desired
  "acts on the redirect" behavior.

> The queue still persists across turns (orchestrator instance lifetime), but with
> revive there is normally nothing left in it at turn end, so cross-turn carryover is
> not a relied-upon path in this slice.

### 3.7 Register the capability + declare the event — `mount()`

Target: `mount()` (`__init__.py:39-57`).

1. Add the event to the existing observability declaration so hooks-logging
   auto-discovers it. In the `register_contributor("observability.events", "loop-streaming", lambda: [...])`
   list, add `"orchestrator:steering_injected"`:

```python
coordinator.register_contributor(
    "observability.events",
    "loop-streaming",
    lambda: [
        "execution:start",
        "execution:end",
        "orchestrator:steering_injected",
    ],
)
```

2. After `await coordinator.mount("orchestrator", orchestrator)`, register the
   capability:

```python
orchestrator = StreamingOrchestrator(config)
await coordinator.mount("orchestrator", orchestrator)
coordinator.register_capability("session.steer", orchestrator.steer)
logger.info("Mounted StreamingOrchestrator with steering capability")
```

The `coordinator` passed to `mount()` is the session coordinator that the app queries —
the same object on which `session.spawn`, `mention_resolver`, etc. are registered — so
registration here is visible to the app's `get_capability("session.steer")`.

**Event name decision: `orchestrator:steering_injected`** (not `agent:steering_injected`).
Justification: this module's own events use the `orchestrator:` / `execution:`
namespaces (`orchestrator:rate_limit_delay`, `execution:start`). The `agent:` namespace
belongs to the loop-agent's session semantics. Staying in-namespace keeps observability
declarations and log routing consistent for this orchestrator.

---

## 4. App-CLI changes (`amplifier-app-cli`)

### 4.1 New: stdin arbiter (approval vs steering)

Both the approval provider and the steering reader read stdin. They must never both
consume the same keystrokes. Introduce a tiny shared arbiter that makes **approval the
priority owner** of stdin.

New file `amplifier_app_cli/stdin_arbiter.py`:

```python
"""Coordinates exclusive stdin access between approval prompts and the
mid-turn steering reader. Approval is the priority owner: while an approval is
in flight, the steering reader suspends entirely."""

from __future__ import annotations


class StdinArbiter:
    def __init__(self) -> None:
        self._approval_active = False

    @property
    def approval_active(self) -> bool:
        return self._approval_active

    def begin_approval(self) -> None:
        self._approval_active = True

    def end_approval(self) -> None:
        self._approval_active = False
```

A plain bool is sufficient: the signal is set synchronously by the approval provider
before it renders the prompt, and the steering reader treats it as authoritative.

### 4.2 `approval_provider.py` — claim stdin during approval

Target: `CLIApprovalProvider` (`approval_provider.py`).

- Constructor gains an optional arbiter:

```python
def __init__(self, console: Console, arbiter: "StdinArbiter | None" = None):
    self.console = console
    self._arbiter = arbiter
```

- In `request_approval`, set the flag **before** rendering the panel (so it is set well
  before any keystroke could arrive) and clear it in a `finally`. Wrap the existing body:

```python
async def request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
    if self._arbiter is not None:
        self._arbiter.begin_approval()
    try:
        # ... existing body: render panel, _get_user_input(), build response ...
        return ApprovalResponse(approved=approved, reason=...)
    finally:
        if self._arbiter is not None:
            self._arbiter.end_approval()
```

`_get_user_input()` keeps reading via `loop.run_in_executor(None, Confirm.ask)`
(`approval_provider.py:128`) — unchanged. While the flag is set, the steering reader
will not touch stdin, so `Confirm.ask` owns it exclusively.

### 4.3 `session_runner.py` — wire the arbiter

Target: Step 10, approval-provider registration (`session_runner.py:276-283`).

```python
# Step 10: Register approval provider (app-layer policy)
from .approval_provider import CLIApprovalProvider
from .stdin_arbiter import StdinArbiter

arbiter = StdinArbiter()
session.coordinator.register_capability("cli.stdin_arbiter", arbiter)

register_provider = session.coordinator.get_capability("approval.register_provider")
if register_provider:
    approval_provider = CLIApprovalProvider(console, arbiter=arbiter)
    register_provider(approval_provider)
    logger.debug("Registered CLIApprovalProvider for interactive approvals")
```

Registering the arbiter as a capability lets the steering reader (in `main.py`) discover
it without a hard import path between the two.

### 4.4 `main.py` — concurrent stdin reader during a turn

Target: `_execute_with_interrupt(prompt_text)` (`main.py:2753`). Currently it creates
`execute_task` (`main.py:2799`) and polls with a 50 ms loop until done (`main.py:2802-2807`).

Add a **POSIX `select`-based** stdin reader that runs only for the duration of the turn,
forwards each non-empty line to `session.steer`, and tears down cleanly. `select` (with a
short timeout) is used instead of a blocking `sys.stdin.readline` in an executor so that
teardown is prompt and leaves **no outstanding blocking read** — the root cause of leaked
threads and stolen next-prompt input.

New module-level helper:

```python
import select

async def _steering_reader(steer_cap, arbiter, stop_event, console) -> None:
    """Read stdin lines during a running turn and forward to session.steer.

    Uses select() with a short timeout so it never holds a blocking read across
    teardown. Suspends entirely while an approval prompt owns stdin.
    """
    loop = asyncio.get_event_loop()
    while not stop_event.is_set():
        # Approval owns stdin while active — do not read.
        if arbiter is not None and arbiter.approval_active:
            await asyncio.sleep(0.05)
            continue
        # Bounded readiness check off the event-loop thread; returns within 0.1s
        # so stop_event is honored promptly (no leaked blocking read at teardown).
        ready = await loop.run_in_executor(None, _stdin_ready, 0.1)
        if not ready:
            continue
        # Re-check: approval may have claimed stdin between select and read.
        if arbiter is not None and arbiter.approval_active:
            continue
        line = sys.stdin.readline()  # data is ready → returns a full TTY line
        if line == "":  # EOF
            break
        text = line.strip()
        if not text:
            continue  # ignore blank / whitespace-only lines
        if steer_cap is None:
            # Fail loud — never silently drop a typed line.
            console.print(
                "[yellow]Steering unavailable: this orchestrator does not "
                "support session.steer.[/yellow]"
            )
            continue
        try:
            steer_cap(text)
            console.print("[dim]queued — applies after current step[/dim]")
        except Exception as e:  # ValueError, SteeringQueueFull, etc.
            console.print(f"[red]Steering rejected: {e}[/red]")


def _stdin_ready(timeout: float) -> bool:
    """True if stdin has data within `timeout` seconds (POSIX)."""
    try:
        r, _, _ = select.select([sys.stdin], [], [], timeout)
        return bool(r)
    except (OSError, ValueError):
        return False
```

Wire it into `_execute_with_interrupt`:

```python
# After resetting cancellation, before/around creating execute_task:
steer_cap = session.coordinator.get_capability("session.steer")
arbiter = session.coordinator.get_capability("cli.stdin_arbiter")
stop_event = asyncio.Event()
reader_task = asyncio.create_task(
    _steering_reader(steer_cap, arbiter, stop_event, console)
)

try:
    execute_task = asyncio.create_task(session.execute(prompt_text))
    # ... existing poll loop and result handling, unchanged ...
finally:
    signal.signal(signal.SIGINT, original_handler)   # existing line
    stop_event.set()
    reader_task.cancel()
    try:
        await reader_task
    except asyncio.CancelledError:
        pass
```

Behavioural notes:
- **Fail-loud-no-capability (constraint 8):** if `steer_cap is None`, the reader still
  captures lines and prints a visible "unavailable" message — typed lines are never
  silently dropped.
- **Arbitration (constraint 9):** approval sets `arbiter.approval_active` before
  rendering its panel; the reader checks the flag both before the `select` and again
  before `readline`, so an in-flight approval prompt always wins stdin. Because the user
  must read the panel before typing, the flag is reliably set long before any approval
  keystroke arrives.
- **Teardown (constraint 11):** `select` returns within 0.1 s, so once `stop_event` is
  set the reader exits without an outstanding blocking read; `reader_task.cancel()` +
  `await` guarantees it is finished before `_execute_with_interrupt` returns, so it
  cannot consume the next REPL prompt's input. This runs on normal completion, on
  graceful/immediate cancel (Ctrl-C), and on error, because it is in the `finally`.
- **Minimal UX (constraint 10):** one-line ack `queued — applies after current step` on
  successful enqueue. (TTY caveat: when `select` signals readable, a cooked terminal has
  delivered a full line, so `readline` returns immediately; documented limitation, see §6.)

### 4.5 No change to the REPL loop

The reader exists only inside `_execute_with_interrupt`. The REPL's
`prompt_session.prompt_async()` (`main.py:2898`) continues to own stdin between turns,
and the reader is guaranteed torn down before control returns to it.

---

## 5. End-to-end proof (local checkpoint)

**Scenario (reproducible):**
1. Start the CLI on `feat/steering` with the streaming orchestrator and a multi-tool
   bundle.
2. Prompt: *"List every file in this directory, then read each one and write a
   one-paragraph summary of each."* This produces several sequential tool rounds.
3. While the first tool round is executing, type and press Enter:
   *"Stop — don't summarize. Just tell me the total number of files and nothing else."*
4. Observe the one-line ack `queued — applies after current step`.

**Observable "acted on" signal — BOTH must hold:**
- **(a) Event:** `events.jsonl` contains an `orchestrator:steering_injected` record whose
  `content` equals the typed redirect, positioned *after* a tool round and *before* the
  next `provider:request`. (And the transcript shows a `role:"user"` message with that
  text inserted after the in-flight round's tool results.)
- **(b) Behavioral change:** the agent abandons the per-file-summary plan and instead
  reports the file count — a change in its subsequent tool calls / final answer directly
  attributable to the steer.

"Acted on" = (a) AND (b). Arrival alone (a) is necessary but not sufficient; the
behavioral divergence (b) is the success criterion. The top-of-iteration drain
guarantees the message lands at the next boundary even if typed late in a round; the
revive rule (§3.6) guarantees action even if typed during the final generation.

---

## 6. Deferred (explicitly out of this slice)

- **FollowUpQueue** and post-turn follow-up semantics.
- **Drop / cancel a queued steer** before it is drained.
- **Child / sub-agent steer targeting** (steering a specific delegate).
- **Windows stdin**: the reader is POSIX (`select` on `sys.stdin`). Windows support is a
  documented limitation for this slice.
- **Live steering in the streaming token branch with concurrent tool calls**: the
  current `_stream_from_provider` path is text-only (`_has_pending_tools` returns
  `False`); multi-tool turns run through the `provider.complete` path, which is where
  the drain and revive operate. No change to the token branch beyond the shared
  top-of-iteration drain.

---

## 7. Test list (unit-level)

### Orchestrator (`amplifier-module-loop-streaming`)
1. **Bounded overflow:** fill `SteeringQueue` to `maxsize`; next `steer()` raises
   `SteeringQueueFull`.
2. **Empty/whitespace rejection:** `steer("")`, `steer("   ")`, `steer("\n")` each raise
   `ValueError`; nothing enqueued.
3. **FIFO order:** enqueue `a, b, c`; `drain()` returns `["a","b","c"]`; `_drain_steering`
   appends user messages to context in that order.
4. **Inject + event:** after `_drain_steering`, context holds the user messages and one
   `orchestrator:steering_injected` event per message with correct
   `content` / `iteration` / `queued_remaining`.
5. **Top-of-iteration drain before provider call:** mock provider returns a tool call
   then text (2 rounds); enqueue a steer during round 1; assert the injected user message
   is present in the messages passed to the *second* `provider.complete` (drain happened
   before `get_messages_for_request`).
6. **Last-drain-edge revive:** provider returns no tool calls on the first call; a steer
   is queued before the break-check → loop performs one more provider call and the steer
   is injected. With an empty queue, the loop breaks normally (no extra call).
7. **Capability registered in mount:** after `mount()`,
   `coordinator.get_capability("session.steer")` is callable and enqueues onto the
   orchestrator's queue.
8. **Streaming undisturbed:** with the streaming/token path and an empty queue, tokens
   still stream and `_drain_steering` is a no-op (no messages, no events).

### App-CLI (`amplifier-app-cli`)
9. **Fail-loud-no-capability:** `get_capability("session.steer")` → `None`; a captured
   stdin line prints the visible "Steering unavailable" message and does not crash.
10. **Steer on line:** stdin yields `"do X\n"`; `steer_cap` called once with `"do X"`;
    ack printed.
11. **Empty line ignored:** blank/whitespace lines do not call `steer_cap`.
12. **Stdin/approval arbitration:** with `arbiter.approval_active == True`, the reader
    does not read stdin / does not call `steer_cap`; after `end_approval()`, it resumes.
13. **Teardown, no leak:** after `_execute_with_interrupt` completes (and on
    cancel/error), `reader_task` is done; `stop_event` honored within the bounded select
    interval; the next prompt's input is not consumed.
14. **Overflow surfaced:** `steer_cap` raises `SteeringQueueFull` → reader prints a
    visible rejection and the turn continues.

---

## 8. Constraints honored

- **Ruthless simplicity:** one queue, one injection path (top-of-iteration), one
  capability stud. The arbiter is a single bool.
- **No fallbacks / synthetics — fail loud:** overflow and empty input raise; missing
  capability is surfaced to the user; no silent drops.
- **Bricks & studs:** the orchestrator has zero knowledge of the app; the app has zero
  knowledge of the orchestrator's internals. The only contract is the `session.steer`
  capability (+ the `cli.stdin_arbiter` capability internal to the app).

---

## 9. UX: anchored input + queued badge

**Status:** Implemented in `amplifier_app_cli/steering_input.py`  
**Branch:** `feat/steering`

### What was built

The raw-TTY `select`-based stdin reader (`_stdin_ready` + `_steering_reader` in
`main.py`) was replaced with a `prompt_toolkit`-based `SteeringInputManager` in the
new module `amplifier_app_cli/steering_input.py`.

**Anchored input line** — During a turn, `SteeringInputManager.run()` runs as a
concurrent `asyncio` task. It calls `PromptSession.prompt_async()` with a
`message=HTML("<ansiblue>  steer: </ansiblue>")` and a `bottom_toolbar` callable.
The prompt line is pinned at the bottom of the terminal; all agent output (Rich
`console.print` from hooks and `CLIDisplaySystem`) is wrapped in `patch_stdout()` and
appears above it.

**patch_stdout scope** — `patch_stdout()` is now activated in
`_execute_with_interrupt` for the *entire* turn (not just the REPL wait between
turns). Rich's `Console.file` property reads `sys.stdout` dynamically at write time
(`self._file` is `None` by default for the singleton created at module import), so
patched output is picked up automatically — no changes to `console.py` were required.

**Queued-message badge** — `SteeringInputManager` maintains a `_pending_count`
integer:
- Incremented by 1 inside `_enqueue()` after each successful `steer_cap()` call.
- Decremented by 1 in `on_steering_injected()`, which is registered as a hook on
  `"orchestrator:steering_injected"` (one event == one drained message) in
  `_execute_with_interrupt`.
- The `_toolbar()` callable returns `"⧗ N message(s) queued · applies after the
  current step"` when `N > 0`, and an empty string (no strip rendered) when `N == 0`.
- After each decrement, `PromptSession.app.invalidate()` is called to refresh the
  toolbar immediately. (`PromptSession.app` is the persistent `Application` object
  created once in `__init__` and reused across `prompt_async()` calls;
  `Application.invalidate()` is thread-safe via `loop.call_soon_threadsafe`.)

**Scrollback ack** — On successful enqueue, a `[dim]⧗ queued: <text>[/dim]` line
is printed to the scrollback via `console.print()` (which flows through
`patch_stdout()` and appears above the pinned prompt).

### How each correctness interaction is handled

| # | Interaction | Mechanism |
|---|-------------|-----------|
| 1 | `patch_stdout` active for the whole turn | `with patch_stdout():` wraps the reader task creation AND `execute_task` in `_execute_with_interrupt`. Rich's `Console.file` is a dynamic property (`self._file or sys.stdout`), so the proxy is picked up at write time without any console.py changes. |
| 2 | StdinArbiter / approval contention | When `arbiter.approval_active` becomes `True` while `prompt_async()` is awaiting input, a watcher inside the `run()` polling loop cancels the `prompt_task` (releasing the terminal from prompt_toolkit's raw mode), then polls until `approval_active` is `False`, then restarts the outer loop. `Confirm.ask` (running in a thread executor) gets exclusive stdin access. |
| 3 | Ctrl-C must still cancel the turn | The `sigint_handler` installed by `_execute_with_interrupt` fires synchronously via `signal.signal` when SIGINT arrives and updates the cancellation token regardless of whether the steering prompt is active. `KeyboardInterrupt` raised from `prompt_async()` by prompt_toolkit's key binding is caught in the `run()` loop and handled with `continue` — the signal handler has already set the graceful/immediate state. |
| 4 | Teardown | The `finally` block inside `with patch_stdout():` sets `_stop_event`, cancels and awaits `_reader_task`. This runs on normal completion, graceful/immediate cancel (Ctrl-C), and on error. The outermost `finally` restores the SIGINT handler. The prompt is guaranteed down before control returns to the REPL. |
| 5 | Empty/whitespace ignored; fail-loud; overflow | `_enqueue()` returns early for empty text. If `steer_cap` is `None`, a yellow `[yellow]Steering unavailable[/yellow]` message is printed. If `steer_cap` raises (e.g. `SteeringQueueFull`), a red `[red]Steering rejected: …[/red]` message is printed. No silent drops. |

### Files changed

| File | Change |
|------|--------|
| `amplifier_app_cli/steering_input.py` | **New.** `SteeringInputManager` class with `run()`, `_enqueue()`, `on_steering_injected()`, `_toolbar()`, and `_input_provider` injection for tests. |
| `amplifier_app_cli/main.py` | Removed `import select`, `_stdin_ready()`, `_steering_reader()`. Updated `_execute_with_interrupt`: creates `SteeringInputManager`, registers the decrement hook, wraps the turn with `with patch_stdout():`. |
| `tests/test_steering.py` | Replaced select-based reader tests with 24 unit tests covering counter semantics, empty/whitespace, fail-loud, overflow, arbiter suspension, teardown, and toolbar text. |

### What was NOT verified by unit test (flagged honestly)

- **Visual behavior (pinned input, live badge)** — cannot be confirmed without a real
  terminal. The positioning of the prompt at the bottom and the real-time toolbar
  refresh must be verified in an interactive session with a real TTY.
- **patch_stdout rendering** — the test suite confirms that `console.print` flows
  through the dynamic `Console.file` property, but the visual ordering of output
  above the prompt versus inline can only be checked visually.
- **Ctrl-C interaction with prompt_toolkit** — the tests confirm that
  `KeyboardInterrupt` is caught and the loop continues, but the interplay between
  the OS SIGINT signal and prompt_toolkit's key binding cannot be exercised in a
  headless test environment.
- **Approval contention with real `Confirm.ask`** — the arbiter logic is
  unit-tested via `_input_provider` injection; the actual terminal hand-off between
  `prompt_async()` and a `run_in_executor(Confirm.ask)` requires a TTY to prove.