"""Unit tests for mid-turn steering UX (app-cli side).

Tests the SteeringInputManager introduced in the anchored-input + queued-badge
feature.  All tests cover the logic layer (_enqueue, on_steering_injected,
_prompt_message, run() with injected input) so no real TTY or prompt_toolkit
app is required.

Spec coverage
~~~~~~~~~~~~~
Counter:
  - increments after a successful session.steer() enqueue
  - decrements on each simulated 'orchestrator:steering_injected' callback
  - drains exactly to 0 (not negative) when all steers are injected

Empty / whitespace:
  - blank and whitespace-only lines are silently ignored

Fail-loud:
  - steer_cap is None  → visible "unavailable" message
  - steer_cap raises SteeringQueueFull → visible "rejected" message

Arbiter:
  - while approval_active the run() loop does NOT forward input
  - after end_approval() the reader resumes and processes the next line

Teardown:
  - setting stop_event causes run() to exit within the poll interval

Toolbar text:
  - empty string when no messages queued
  - badge text including count when messages are queued

StdinArbiter and CLIApprovalProvider integration tests are preserved unchanged.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from amplifier_app_cli.stdin_arbiter import StdinArbiter
from amplifier_app_cli.steering_input import SteeringInputManager


# ---------------------------------------------------------------------------
# Stub for SteeringQueueFull (produced by the orchestrator, not a test dep)
# ---------------------------------------------------------------------------


class _SteeringQueueFull(RuntimeError):
    """Test stub for SteeringQueueFull."""


# ---------------------------------------------------------------------------
# Minimal dispatcher stand-in — calls handlers the way the Amplifier kernel
# does: handler(event_type: str, payload: dict).
#
# A future signature mismatch on the handler will raise TypeError here,
# ensuring this test path catches bugs like Defect 1 (wrong arg count).
# ---------------------------------------------------------------------------


class _FakeHooksRegistry:
    """Faithful stand-in for the Amplifier hook dispatcher.

    Registers handlers and calls them with the real kernel convention:
    ``handler(event_type: str, payload: dict)``.

    A handler registered with the wrong number of arguments will raise
    ``TypeError`` at emit time, matching what the real dispatcher does.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list] = {}

    def register(self, event: str, handler: Any, **kwargs: Any) -> None:
        self._handlers.setdefault(event, []).append(handler)

    async def emit(self, event: str, payload: dict) -> None:
        for handler in self._handlers.get(event, []):
            await handler(event, payload)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_console() -> MagicMock:
    return MagicMock()


def _make_manager(
    steer_cap=None,
    arbiter=None,
    *,
    stop_event: asyncio.Event | None = None,
    input_provider=None,
) -> tuple[SteeringInputManager, asyncio.Event]:
    """Create a SteeringInputManager wired for testing."""
    stop = stop_event or asyncio.Event()
    console = _make_console()
    manager = SteeringInputManager(
        steer_cap,
        arbiter,
        stop,
        console,
        _input_provider=input_provider,
    )
    return manager, stop


# ---------------------------------------------------------------------------
# Counter: increments on enqueue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_counter_increments_on_enqueue():
    """Successful steer enqueue increments pending_count by 1."""
    steer_cap = MagicMock()
    manager, _ = _make_manager(steer_cap=steer_cap)

    assert manager.pending_count == 0
    await manager._enqueue("do X")
    assert manager.pending_count == 1
    steer_cap.assert_called_once_with("do X")


@pytest.mark.asyncio
async def test_counter_increments_multiple_times():
    """Each successful enqueue increments the counter independently."""
    steer_cap = MagicMock()
    manager, _ = _make_manager(steer_cap=steer_cap)

    await manager._enqueue("first")
    await manager._enqueue("second")
    await manager._enqueue("third")
    assert manager.pending_count == 3


# ---------------------------------------------------------------------------
# Counter: decrements on orchestrator:steering_injected hook callback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_counter_decrements_on_injected_hook():
    """on_steering_injected() decrements pending_count via the real dispatcher path.

    The handler is registered on a _FakeHooksRegistry and emitted through it
    exactly as the Amplifier kernel does — handler(event_type, payload).
    A wrong handler signature (e.g. missing the ``event`` arg) will raise TypeError
    here, reproducing the Defect 1 crash that was missed by direct-call tests.
    """
    steer_cap = MagicMock()
    manager, _ = _make_manager(steer_cap=steer_cap)

    await manager._enqueue("do X")
    assert manager.pending_count == 1

    registry = _FakeHooksRegistry()
    registry.register("orchestrator:steering_injected", manager.on_steering_injected)
    await registry.emit("orchestrator:steering_injected", {"content": "do X"})
    assert manager.pending_count == 0


@pytest.mark.asyncio
async def test_counter_drains_exactly_to_zero():
    """Enqueueing N messages and injecting N events via the dispatcher leaves counter at 0.

    Uses _FakeHooksRegistry so a signature mismatch fails with TypeError, not
    silently passes as it did before (Defect 1).
    """
    steer_cap = MagicMock()
    manager, _ = _make_manager(steer_cap=steer_cap)

    n = 5
    for i in range(n):
        await manager._enqueue(f"msg {i}")
    assert manager.pending_count == n

    registry = _FakeHooksRegistry()
    registry.register("orchestrator:steering_injected", manager.on_steering_injected)
    for _ in range(n):
        await registry.emit("orchestrator:steering_injected", {})
    assert manager.pending_count == 0


@pytest.mark.asyncio
async def test_counter_never_goes_below_zero():
    """Extra injection events via the dispatcher do not push the counter below 0."""
    manager, _ = _make_manager(steer_cap=MagicMock())

    await manager._enqueue("once")

    registry = _FakeHooksRegistry()
    registry.register("orchestrator:steering_injected", manager.on_steering_injected)
    await registry.emit("orchestrator:steering_injected", {})
    await registry.emit("orchestrator:steering_injected", {})  # extra — must be clamped
    assert manager.pending_count == 0


# ---------------------------------------------------------------------------
# Empty / whitespace ignored
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_string_ignored():
    """Empty string does not call steer_cap or change counter."""
    steer_cap = MagicMock()
    manager, _ = _make_manager(steer_cap=steer_cap)

    await manager._enqueue("")
    steer_cap.assert_not_called()
    assert manager.pending_count == 0


@pytest.mark.asyncio
async def test_whitespace_only_ignored():
    """Whitespace-only strings (spaces, tabs, newlines) are silently ignored."""
    steer_cap = MagicMock()
    manager, _ = _make_manager(steer_cap=steer_cap)

    for text in ("   ", "\t", "\n", "  \t  \n  "):
        await manager._enqueue(text)

    steer_cap.assert_not_called()
    assert manager.pending_count == 0


# ---------------------------------------------------------------------------
# Fail-loud: steer_cap is None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fail_loud_no_capability():
    """When steer_cap is None a visible 'unavailable' message is printed; no crash."""
    manager, _ = _make_manager(steer_cap=None)

    await manager._enqueue("redirect me")

    printed = [str(c) for c in manager._console.print.call_args_list]
    assert any("Steering unavailable" in msg for msg in printed), (
        f"Expected 'Steering unavailable'; got: {printed}"
    )
    assert manager.pending_count == 0


@pytest.mark.asyncio
async def test_fail_loud_no_capability_no_crash_on_repeated_calls():
    """Multiple enqueues with no steer_cap each print a visible message."""
    manager, _ = _make_manager(steer_cap=None)

    await manager._enqueue("first")
    await manager._enqueue("second")

    calls = manager._console.print.call_args_list
    printed = [str(c) for c in calls]
    unavailable_count = sum(1 for msg in printed if "Steering unavailable" in msg)
    assert unavailable_count == 2


# ---------------------------------------------------------------------------
# Overflow surfaced
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overflow_surfaced():
    """SteeringQueueFull → visible rejection message; counter unchanged."""
    steer_cap = MagicMock(side_effect=_SteeringQueueFull("queue full"))
    manager, _ = _make_manager(steer_cap=steer_cap)

    await manager._enqueue("do X")

    steer_cap.assert_called_once_with("do X")
    printed = [str(c) for c in manager._console.print.call_args_list]
    assert any("Steering rejected" in msg for msg in printed), (
        f"Expected 'Steering rejected'; got: {printed}"
    )
    # Counter must NOT increment on failure
    assert manager.pending_count == 0


# ---------------------------------------------------------------------------
# Prompt message callable (_prompt_message)
# ---------------------------------------------------------------------------


def test_prompt_message_no_pending_shows_plain_steer():
    """_prompt_message returns a plain 'steer:' prompt when no messages are queued."""
    manager, _ = _make_manager()
    msg = manager._prompt_message()
    # HTML.value holds the raw string; check it contains 'steer' and NOT 'queued'.
    text = msg.value
    assert "steer" in text
    assert "queued" not in text


def test_prompt_message_with_pending_shows_count_and_queued():
    """_prompt_message includes the count and 'queued' when pending_count > 0."""
    manager, _ = _make_manager()
    manager._enqueued_total = 3  # monotonic model: badge = max(0, 3 - 0) = 3
    msg = manager._prompt_message()
    text = msg.value
    assert "queued" in text
    assert "3" in text


@pytest.mark.asyncio
async def test_prompt_message_updates_after_decrement_via_dispatcher():
    """_prompt_message reflects the count after a dispatcher-path decrement.

    Simulates the full round-trip: counter set to 1, dispatcher drains it,
    _prompt_message returns the updated (zero) prompt without 'queued'.
    """
    steer_cap = MagicMock()
    manager, _ = _make_manager(steer_cap=steer_cap)

    # Manually set enqueued counter (bypassing _enqueue to avoid a real prompt_toolkit App)
    manager._enqueued_total = 1  # monotonic model: badge = max(0, 1 - 0) = 1
    assert "queued" in manager._prompt_message().value

    registry = _FakeHooksRegistry()
    registry.register("orchestrator:steering_injected", manager.on_steering_injected)
    await registry.emit("orchestrator:steering_injected", {"content": "msg"})

    assert manager.pending_count == 0
    assert "queued" not in manager._prompt_message().value


# ---------------------------------------------------------------------------
# run() with injected input: arbiter releases reader when approval_active
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_arbiter_suspends_reader_during_approval():
    """While arbiter.approval_active the run() loop does not forward input."""
    input_queue: asyncio.Queue[str] = asyncio.Queue()

    async def mock_input() -> str:
        return await input_queue.get()

    steer_cap = MagicMock()
    arbiter = StdinArbiter()
    stop_event = asyncio.Event()
    console = _make_console()

    manager = SteeringInputManager(
        steer_cap,
        arbiter,
        stop_event,
        console,
        _input_provider=mock_input,
    )

    # Activate approval before starting run()
    arbiter.begin_approval()
    run_task = asyncio.create_task(manager.run())

    # Give the loop a chance to spin a few times — approval is active
    await asyncio.sleep(0.15)
    assert not steer_cap.called, (
        "steer_cap must not be called while approval_active is True"
    )

    # Release approval and deliver one line of input
    arbiter.end_approval()
    await input_queue.put("do X")

    # Wait for the line to be processed
    await asyncio.sleep(0.15)

    # Cleanup
    stop_event.set()
    run_task.cancel()
    try:
        await asyncio.wait_for(run_task, timeout=1.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass

    steer_cap.assert_called_once_with("do X")


@pytest.mark.asyncio
async def test_arbiter_resumes_after_approval_ends():
    """After end_approval() the reader processes the next queued line."""
    input_queue: asyncio.Queue[str] = asyncio.Queue()

    async def mock_input() -> str:
        return await input_queue.get()

    steer_cap = MagicMock()
    arbiter = StdinArbiter()
    stop_event = asyncio.Event()
    console = _make_console()

    manager = SteeringInputManager(
        steer_cap,
        arbiter,
        stop_event,
        console,
        _input_provider=mock_input,
    )

    run_task = asyncio.create_task(manager.run())

    # Put a line while approval is inactive — should be processed
    await input_queue.put("before approval")
    await asyncio.sleep(0.15)
    assert steer_cap.call_count == 1

    # Activate approval, put a second line — should NOT be processed yet
    arbiter.begin_approval()
    await input_queue.put("during approval")
    await asyncio.sleep(0.15)
    # Prompt was aborted for approval; "during approval" is still in the queue
    # but the run loop is waiting for approval to end.
    # Because we put "during approval" into the queue WHILE approval was active
    # and the prompt task was cancelled, it will be picked up once approval ends.

    # Release approval
    arbiter.end_approval()
    await asyncio.sleep(0.2)

    stop_event.set()
    run_task.cancel()
    try:
        await asyncio.wait_for(run_task, timeout=1.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass

    # Both messages should have been processed
    assert steer_cap.call_count >= 2


# ---------------------------------------------------------------------------
# run() teardown: stop_event causes clean exit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_teardown_on_stop_event():
    """Setting stop_event causes run() to exit within the poll interval."""
    # mock_input blocks indefinitely so the loop is in the polling phase
    never_returns: asyncio.Event = asyncio.Event()

    async def mock_input() -> str:
        await never_returns.wait()  # blocks until we set it
        return ""

    manager, stop = _make_manager(input_provider=mock_input)
    run_task = asyncio.create_task(manager.run())

    # Let the prompt task start
    await asyncio.sleep(0.05)

    # Signal stop
    stop.set()

    # run() should exit within ~100 ms (one poll cycle)
    await asyncio.wait_for(run_task, timeout=1.0)
    assert run_task.done()


@pytest.mark.asyncio
async def test_teardown_cancel_is_clean():
    """Cancelling the run() task does not raise unhandled exceptions."""
    never_returns: asyncio.Event = asyncio.Event()

    async def mock_input() -> str:
        await never_returns.wait()
        return ""

    manager, _ = _make_manager(input_provider=mock_input)
    run_task = asyncio.create_task(manager.run())

    await asyncio.sleep(0.05)
    run_task.cancel()

    try:
        await asyncio.wait_for(run_task, timeout=1.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass

    assert run_task.done()


# ---------------------------------------------------------------------------
# Ctrl-C regression: _CtrlCInterrupt must not escape run() as a crash
# ---------------------------------------------------------------------------
# Background: the old code used PromptSession(interrupt_exception=KeyboardInterrupt).
# When Ctrl-C was pressed, prompt_toolkit called app.exit(exception=KeyboardInterrupt()).
# That KeyboardInterrupt propagated through the prompt_async() coroutine and into
# asyncio's Task.__step_run_and_handle_result(), which:
#
#     except (KeyboardInterrupt, SystemExit) as exc:
#         super().set_exception(exc)
#         raise   ← re-raised!
#
# The re-raise escaped through Handle._run() (also re-raises KeyboardInterrupt),
# through EventLoop._run_once(), and straight into asyncio.run() — crashing the
# process.  No try/except in user code can intercept it at that stage.
#
# Fix: PromptSession(interrupt_exception=_CtrlCInterrupt) where _CtrlCInterrupt
# is a plain Exception subclass.  asyncio stores it normally (no re-raise), so
# await prompt_task raises _CtrlCInterrupt in the normal place and the
# except _CtrlCInterrupt: continue clause in run() handles it.
# ---------------------------------------------------------------------------


def test_ctrl_c_interrupt_sentinel_is_plain_exception():
    """_CtrlCInterrupt must be a regular Exception, not a BaseException.

    If it were KeyboardInterrupt (or any BaseException that is not also an
    Exception), asyncio's Task.__step_run_and_handle_result() would re-raise it
    after storing it, crashing asyncio.run().
    """
    from amplifier_app_cli.steering_input import _CtrlCInterrupt

    assert issubclass(_CtrlCInterrupt, Exception), (
        "_CtrlCInterrupt must be a plain Exception subclass so asyncio's task "
        "machinery does NOT re-raise it"
    )
    assert not issubclass(_CtrlCInterrupt, KeyboardInterrupt), (
        "_CtrlCInterrupt must NOT be KeyboardInterrupt; using KeyboardInterrupt "
        "triggers the Task.__step_run_and_handle_result() re-raise bug"
    )


@pytest.mark.asyncio
async def test_ctrl_c_during_prompt_does_not_crash_run_loop():
    """_CtrlCInterrupt raised by the prompt is caught; run() continues normally.

    Regression test for: Ctrl-C during the steering prompt crashed
    asyncio.run() because KeyboardInterrupt escaped asyncio's task machinery.

    The fix uses PromptSession(interrupt_exception=_CtrlCInterrupt).  This
    test verifies that:
    1. When the input provider raises _CtrlCInterrupt, run() does NOT exit/crash.
    2. run() continues processing and enqueues the NEXT input normally.
    """
    from amplifier_app_cli.steering_input import _CtrlCInterrupt

    call_count = 0
    # Gate that fires when steer_cap is actually invoked (not just when the
    # input coroutine returns — the enqueue happens one await later).
    steer_called = asyncio.Event()

    captured_texts: list[str] = []

    def recording_steer_cap(text: str) -> None:
        captured_texts.append(text)
        steer_called.set()

    async def mock_input() -> str | None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Simulate Ctrl-C: prompt_async() would raise _CtrlCInterrupt
            raise _CtrlCInterrupt()
        if call_count == 2:
            # Normal input on the next iteration — proves run() continued
            return "steer text"
        # Block so stop_event can terminate cleanly
        await asyncio.sleep(10)
        return None

    stop = asyncio.Event()
    manager = SteeringInputManager(
        steer_cap=recording_steer_cap,
        arbiter=None,
        stop_event=stop,
        console=MagicMock(),
        _input_provider=mock_input,
    )

    run_task = asyncio.create_task(manager.run())

    # Wait until steer_cap is actually called (proves loop continued + enqueued)
    await asyncio.wait_for(steer_called.wait(), timeout=1.0)

    # "steer text" was forwarded to steer_cap (loop continued after _CtrlCInterrupt)
    assert captured_texts == ["steer text"], (
        f"Expected ['steer text'] after Ctrl-C recovery, got {captured_texts}"
    )

    # run() must still be alive (stop_event not yet set, not crashed)
    assert not run_task.done(), "run() unexpectedly exited after _CtrlCInterrupt"

    # Clean up
    stop.set()
    run_task.cancel()
    try:
        await asyncio.wait_for(run_task, timeout=1.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass


# ---------------------------------------------------------------------------
# StdinArbiter unit tests (unchanged)
# ---------------------------------------------------------------------------


def test_arbiter_starts_inactive():
    arbiter = StdinArbiter()
    assert not arbiter.approval_active


def test_arbiter_begin_end():
    arbiter = StdinArbiter()
    arbiter.begin_approval()
    assert arbiter.approval_active
    arbiter.end_approval()
    assert not arbiter.approval_active


def test_arbiter_multiple_cycles():
    arbiter = StdinArbiter()
    for _ in range(3):
        arbiter.begin_approval()
        assert arbiter.approval_active
        arbiter.end_approval()
        assert not arbiter.approval_active


# ---------------------------------------------------------------------------
# CLIApprovalProvider arbiter integration (unchanged)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approval_provider_sets_arbiter():
    """Approval provider sets arbiter.approval_active before and clears after."""
    from amplifier_app_cli.approval_provider import CLIApprovalProvider
    from amplifier_core import ApprovalRequest

    arbiter = StdinArbiter()
    console = MagicMock()
    console.width = 80

    provider = CLIApprovalProvider(console, arbiter=arbiter)  # type: ignore[call-arg]

    states_during: list[bool] = []

    async def fake_do_request(req):
        states_during.append(arbiter.approval_active)
        from amplifier_core import ApprovalResponse

        return ApprovalResponse(approved=True, reason="ok")

    request = ApprovalRequest(
        tool_name="test_tool",
        action="test_action",
        risk_level="low",
    )

    with patch.object(provider, "_do_request_approval", side_effect=fake_do_request):
        assert not arbiter.approval_active
        await provider.request_approval(request)
        assert not arbiter.approval_active

    assert states_during == [True]


@pytest.mark.asyncio
async def test_approval_provider_clears_arbiter_on_exception():
    """Arbiter is cleared even if _do_request_approval raises."""
    from amplifier_app_cli.approval_provider import CLIApprovalProvider
    from amplifier_core import ApprovalRequest

    arbiter = StdinArbiter()
    console = MagicMock()
    console.width = 80

    provider = CLIApprovalProvider(console, arbiter=arbiter)  # type: ignore[call-arg]

    request = ApprovalRequest(
        tool_name="t",
        action="a",
        risk_level="low",
    )

    async def boom(_req):
        raise RuntimeError("something went wrong")

    with patch.object(provider, "_do_request_approval", side_effect=boom):
        with pytest.raises(RuntimeError):
            await provider.request_approval(request)

    assert not arbiter.approval_active


# ===========================================================================
# Spec §4.2 — app-cli drain-semantics hardening (steering-drain.md)
# A1–A6: failing-test-first per spec
# ===========================================================================


# ---------------------------------------------------------------------------
# A1: badge == enqueued_total − injected_total (FAILS under old code)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_badge_is_enqueued_minus_injected():
    """Badge = max(0, _enqueued_total - _injected_total) at every step.

    The monotonic pair (_enqueued_total, _injected_total) must be the
    authoritative source of truth for pending_count.  Interleaves enqueue
    and inject in several orders and asserts the invariant at each step.

    **FAILS under old code** (lone _pending_count): _enqueued_total and
    _injected_total do not exist → AttributeError.
    """
    steer_cap = MagicMock()
    manager, _ = _make_manager(steer_cap=steer_cap)

    # Initial state
    assert manager._enqueued_total == 0  # AttributeError on old code → FAIL
    assert manager._injected_total == 0
    assert manager.pending_count == 0

    # Enqueue first steer
    await manager._enqueue("alpha")
    assert manager._enqueued_total == 1
    assert manager._injected_total == 0
    assert manager.pending_count == max(0, 1 - 0)  # 1

    # Inject it
    await manager.on_steering_injected("orchestrator:steering_injected", {})
    assert manager._enqueued_total == 1
    assert manager._injected_total == 1
    assert manager.pending_count == max(0, 1 - 1)  # 0

    # Enqueue two more in quick succession
    await manager._enqueue("beta")
    await manager._enqueue("gamma")
    assert manager._enqueued_total == 3
    assert manager._injected_total == 1
    assert manager.pending_count == max(0, 3 - 1)  # 2

    # Drain one
    await manager.on_steering_injected("orchestrator:steering_injected", {})
    assert manager.pending_count == max(0, 3 - 2)  # 1

    # Drain the last
    await manager.on_steering_injected("orchestrator:steering_injected", {})
    assert manager.pending_count == max(0, 3 - 3)  # 0
    assert manager._enqueued_total == 3
    assert manager._injected_total == 3


# ---------------------------------------------------------------------------
# A2: badge never negative — regression guard under new model (PASSES now)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extra_injected_event_cannot_drive_badge_negative():
    """Extra inject events beyond enqueued count cannot drive badge below 0.

    PASSES under old code (guard ``if _pending_count > 0``) and must continue
    to pass under the monotonic model (``max(0, ...)``).  Added as an
    explicit regression guard per spec §3.4.
    """
    steer_cap = MagicMock()
    manager, _ = _make_manager(steer_cap=steer_cap)

    registry = _FakeHooksRegistry()
    registry.register("orchestrator:steering_injected", manager.on_steering_injected)

    await manager._enqueue("one")
    assert manager.pending_count == 1

    # Fire 5 inject events — 4 more than enqueued
    for _ in range(5):
        await registry.emit("orchestrator:steering_injected", {})

    assert manager.pending_count == 0
    assert manager.pending_count >= 0  # explicit non-negative guard


# ---------------------------------------------------------------------------
# A3: lost inject event does not stick phantom (FAILS under old code)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lost_injected_event_does_not_stick_phantom():
    """Monotonic pair self-heals: a late inject event drives the badge to 0.

    Scenario: two steers are enqueued; one inject event is delayed (simulating
    a lost/out-of-order event).  The badge reflects the true outstanding count
    while the event is delayed.  When the delayed event finally arrives, the
    badge self-heals to the correct value.

    Under the old lone-decrement model the internal counter is a single
    opaque int with no memory of how many events were received vs enqueued.
    The monotonic pair makes the state fully inspectable.

    **FAILS under old code**: _enqueued_total / _injected_total do not exist
    → AttributeError.
    """
    steer_cap = MagicMock()
    manager, _ = _make_manager(steer_cap=steer_cap)

    # Enqueue two steers
    await manager._enqueue("msg1")
    await manager._enqueue("msg2")
    assert manager._enqueued_total == 2  # AttributeError on old code → FAIL
    assert manager._injected_total == 0
    assert manager.pending_count == 2

    # Only one inject event arrives (the other is "delayed")
    await manager.on_steering_injected("orchestrator:steering_injected", {})
    assert manager._enqueued_total == 2
    assert manager._injected_total == 1
    assert manager.pending_count == 1  # one genuinely outstanding

    # The delayed inject event finally arrives — badge self-heals to 0
    await manager.on_steering_injected("orchestrator:steering_injected", {})
    assert manager._enqueued_total == 2
    assert manager._injected_total == 2
    assert manager.pending_count == 0  # no phantom "1 queued" remains


# ---------------------------------------------------------------------------
# A4: overflow shows visible rejection — regression guard (PASSES now)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overflow_shows_visible_rejection():
    """SteeringQueueFull raises a visible rejection; badge does NOT increment.

    PASSES under old code (already working).  Added as explicit regression
    guard per spec §3.1.
    """
    steer_cap = MagicMock(side_effect=_SteeringQueueFull("queue full"))
    manager, _ = _make_manager(steer_cap=steer_cap)

    await manager._enqueue("too much")

    steer_cap.assert_called_once_with("too much")

    printed = [str(c) for c in manager._console.print.call_args_list]
    assert any("rejected" in msg.lower() for msg in printed), (
        f"Expected visible 'rejected' message on overflow; got: {printed}"
    )
    # Badge must NOT increment on failure
    assert manager.pending_count == 0


# ---------------------------------------------------------------------------
# A5: turn-end steer is NOT sent, and the ack is honest (no false promise)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_turn_end_steer_is_not_sent_and_ack_is_honest():
    """When stop_event is set (turn ending/ended), a steer would be wiped by the
    orchestrator's execute()-entry clear() before the next turn — so _enqueue must
    NOT forward it and must NOT promise "next turn". It acks honestly instead.

    Guards against the false-promise bug a DTU run caught: acking
    "queued for next turn" while the orchestrator discards the steer.
    """
    steer_cap = MagicMock()
    stop = asyncio.Event()
    manager, _ = _make_manager(steer_cap=steer_cap, stop_event=stop)

    # Simulate turn-ending/ended state
    stop.set()

    await manager._enqueue("steer at turn end")

    # NOT forwarded — the orchestrator would clear it at next execute() entry.
    steer_cap.assert_not_called()

    # Badge must NOT increment for an undelivered steer (no phantom "1 queued").
    assert manager.pending_count == 0

    # Ack is honest: says it was not sent, and does NOT falsely promise "next turn".
    printed = [str(c) for c in manager._console.print.call_args_list]
    assert any("not sent" in msg.lower() for msg in printed), (
        f"Expected an honest 'not sent' ack when the turn has ended; got: {printed}"
    )
    assert not any("next turn" in msg.lower() for msg in printed), (
        f"Ack must NOT falsely promise 'next turn'; got: {printed}"
    )


@pytest.mark.asyncio
async def test_live_turn_steer_ack_does_not_say_next_turn():
    """When stop_event is NOT set (turn live), the ack is ``⧗ queued: …``
    without any "next turn" text.

    Companion guard for A5 — verifies the normal live-turn path is unchanged.
    PASSES under both old and new code.
    """
    steer_cap = MagicMock()
    stop = asyncio.Event()
    manager, _ = _make_manager(steer_cap=steer_cap, stop_event=stop)

    # stop_event NOT set — turn is live
    assert not stop.is_set()

    await manager._enqueue("live steer")

    steer_cap.assert_called_once_with("live steer")

    printed = [str(c) for c in manager._console.print.call_args_list]
    assert any("queued" in msg.lower() for msg in printed), (
        f"Expected a 'queued' ack for live-turn steer; got: {printed}"
    )
    assert not any("next turn" in msg.lower() for msg in printed), (
        f"Live-turn ack must NOT say 'next turn'; got: {printed}"
    )


# ---------------------------------------------------------------------------
# A6: ANSI/OSC escape split probe — xfail (parked, investigating §3.5)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=False,
    reason=(
        "Probe for §3.5 of steering-drain.md: patch_stdout(raw=True) may "
        "split an ANSI/OSC escape sequence across two writes with a prompt "
        "redraw interleaved. Unconfirmed reproduction — tracking as risk. "
        "Do NOT implement a fix without first reproducing corruption in a "
        "real terminal session. Parked."
    ),
)
@pytest.mark.asyncio
async def test_split_ansi_escape_survives_prompt_redraw():
    """PROBE: A split ANSI/OSC escape written across two flush-calls should
    survive a prompt_toolkit ``app.invalidate()`` redraw interleaved between
    the halves.

    This test cannot be reproduced with the unit-test harness because
    ``_input_provider`` is injected (no real ``_pt_session``, so
    ``invalidate()`` is never called).  It is kept as a tracking placeholder
    so the risk described in §3.5 stays visible in the test suite rather than
    being silently closed.

    Current status: unconfirmed reproduction.  Assess before fixing.
    """
    pytest.xfail(
        "Cannot reproduce with unit-test harness (no real PTY / PromptSession). "
        "Tracking per §3.5 of steering-drain.md. Reassess if real-terminal "
        "corruption is observed."
    )
