"""Unit tests for mid-turn steering (app-cli side).

Covers spec tests 9–14 from docs/designs/steering.md §7 (App-CLI section):

9.  fail-loud-no-capability
10. steer-on-line
11. empty-line-ignored
12. stdin/approval arbitration suspends reader
13. teardown leaves no leak / does not steal next prompt
14. overflow surfaced
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from amplifier_app_cli.main import _steering_reader  # type: ignore[attr-defined]
from amplifier_app_cli.stdin_arbiter import StdinArbiter


# ---------------------------------------------------------------------------
# Stub for SteeringQueueFull (produced by the orchestrator module, which is
# not a test dependency here — we just need something that is a RuntimeError
# subclass to verify the error path).
# ---------------------------------------------------------------------------


class _SteeringQueueFull(RuntimeError):
    """Test stub for SteeringQueueFull."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_console() -> MagicMock:
    return MagicMock()


async def _drive_reader(
    steer_cap,
    arbiter: StdinArbiter | None,
    *,
    ready_values: list[bool],
    readline_values: list[str],
    stop_after_reads: bool = True,
    timeout: float = 5.0,
) -> MagicMock:
    """Run _steering_reader with controlled stdin data, return the console mock.

    *ready_values* controls what _stdin_ready returns on successive calls.
    *readline_values* is the sequence sys.stdin.readline returns.

    The reader is stopped by setting stop_event when ready_values are exhausted
    (or when steer_cap is called, if stop_after_reads=True).
    """
    console = _make_console()
    stop_event = asyncio.Event()

    ready_iter = iter(ready_values)
    exhausted = False

    def fake_ready(_timeout: float) -> bool:
        nonlocal exhausted
        try:
            val = next(ready_iter)
        except StopIteration:
            exhausted = True
            stop_event.set()
            return False
        return val

    with (
        patch("amplifier_app_cli.main._stdin_ready", fake_ready),
        patch("sys.stdin") as mock_stdin,
    ):
        mock_stdin.readline.side_effect = readline_values

        await asyncio.wait_for(
            _steering_reader(steer_cap, arbiter, stop_event, console),
            timeout=timeout,
        )

    return console


# ---------------------------------------------------------------------------
# Test 9 — fail-loud-no-capability
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fail_loud_no_capability():
    """Spec 9: steer_cap is None → visible message, no crash, no silent drop."""
    console = await _drive_reader(
        steer_cap=None,
        arbiter=None,
        ready_values=[True, False],  # ready once, then exhaust
        readline_values=["redirect: do this instead\n"],
    )

    # A visible "unavailable" message must be printed
    printed_messages = [str(call) for call in console.print.call_args_list]
    assert any("Steering unavailable" in msg for msg in printed_messages), (
        f"Expected 'Steering unavailable' in console output; got: {printed_messages}"
    )


# ---------------------------------------------------------------------------
# Test 10 — steer-on-line
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_steer_on_line():
    """Spec 10: a non-empty typed line calls steer_cap and prints ack."""
    steer_cap = MagicMock()
    arbiter = StdinArbiter()
    stop_event = asyncio.Event()
    console = _make_console()

    call_count = 0

    def fake_ready(_timeout: float) -> bool:
        nonlocal call_count
        call_count += 1
        return call_count == 1  # True on first call only

    async def stopper():
        """Wait for steer_cap to be invoked, then stop the reader."""
        while not steer_cap.called:
            await asyncio.sleep(0.005)
        stop_event.set()

    with (
        patch("amplifier_app_cli.main._stdin_ready", fake_ready),
        patch("sys.stdin") as mock_stdin,
    ):
        mock_stdin.readline.return_value = "do X\n"

        await asyncio.gather(
            asyncio.wait_for(
                _steering_reader(steer_cap, arbiter, stop_event, console),
                timeout=5.0,
            ),
            stopper(),
        )

    steer_cap.assert_called_once_with("do X")
    printed = [str(c) for c in console.print.call_args_list]
    assert any("queued" in msg for msg in printed), (
        f"Expected ack in output; got: {printed}"
    )


# ---------------------------------------------------------------------------
# Test 11 — empty-line-ignored
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_line_ignored():
    """Spec 11: blank and whitespace-only lines do not call steer_cap."""
    steer_cap = MagicMock()
    console = await _drive_reader(
        steer_cap=steer_cap,
        arbiter=None,
        ready_values=[True, True, True, False],  # 3 reads, then exhaust
        readline_values=["   \n", "\n", "\t  \n"],
    )

    steer_cap.assert_not_called()
    # No ack should have been printed either
    printed = [str(c) for c in console.print.call_args_list]
    assert not any("queued" in msg for msg in printed)


# ---------------------------------------------------------------------------
# Test 12 — stdin / approval arbitration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_arbitration_suspends_reader_during_approval():
    """Spec 12: while arbiter.approval_active, reader does not call _stdin_ready or steer_cap."""
    steer_cap = MagicMock()
    arbiter = StdinArbiter()
    stop_event = asyncio.Event()
    console = _make_console()

    ready_calls_approval_state: list[bool] = []

    def fake_ready(_timeout: float) -> bool:
        # Record whether approval was active at the time of each _stdin_ready call.
        ready_calls_approval_state.append(arbiter.approval_active)
        return True  # Always data-ready once we get here

    # Phase 1: approval active from the start
    arbiter.begin_approval()

    async def controller():
        # Verify no reads happen during approval
        await asyncio.sleep(0.15)
        assert not steer_cap.called, (
            "steer_cap must not be called while approval is active"
        )
        assert not ready_calls_approval_state, (
            "_stdin_ready must not be called during approval"
        )

        # Phase 2: release approval — reader should now pick up the one queued line
        arbiter.end_approval()

    with (
        patch("amplifier_app_cli.main._stdin_ready", fake_ready),
        patch("sys.stdin") as mock_stdin,
    ):
        # readline returns the line once, then "" (EOF) so the reader exits cleanly
        # without needing stop_event and without calling steer_cap more than once.
        mock_stdin.readline.side_effect = ["do X\n", ""]

        await asyncio.gather(
            asyncio.wait_for(
                _steering_reader(steer_cap, arbiter, stop_event, console),
                timeout=5.0,
            ),
            controller(),
        )

    # After approval ended, the line was processed exactly once
    steer_cap.assert_called_once_with("do X")
    # _stdin_ready was only called when approval was NOT active
    assert all(not was_active for was_active in ready_calls_approval_state), (
        "ready() was called while approval was active"
    )


# ---------------------------------------------------------------------------
# Test 13 — teardown / no stolen next-prompt input
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_teardown_reader_exits_on_stop_event():
    """Spec 13: reader exits cleanly within the bounded select interval after stop_event is set."""
    console = _make_console()
    arbiter = StdinArbiter()
    stop_event = asyncio.Event()

    # _stdin_ready returns False (no data) every call — reader loops without reading
    with patch("amplifier_app_cli.main._stdin_ready", lambda _t: False):

        async def set_stop():
            await asyncio.sleep(0.05)
            stop_event.set()

        # Both tasks must complete: reader exits, stopper finishes
        await asyncio.gather(
            asyncio.wait_for(
                _steering_reader(None, arbiter, stop_event, console),
                timeout=2.0,
            ),
            set_stop(),
        )

    # Reader exited cleanly
    assert stop_event.is_set()


@pytest.mark.asyncio
async def test_teardown_cancel_exits_promptly():
    """Spec 13: reader task can be cancelled and awaited without hanging."""
    arbiter = StdinArbiter()
    stop_event = asyncio.Event()

    with patch("amplifier_app_cli.main._stdin_ready", lambda _t: False):
        task = asyncio.create_task(
            _steering_reader(None, arbiter, stop_event, _make_console())
        )

        # Let it start
        await asyncio.sleep(0.05)

        # Signal stop then cancel (matching the finally block in _execute_with_interrupt)
        stop_event.set()
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=1.0)
        except asyncio.CancelledError:
            pass

        assert task.done()


# ---------------------------------------------------------------------------
# Test 14 — overflow surfaced
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overflow_surfaced():
    """Spec 14: SteeringQueueFull → reader prints visible rejection; turn continues."""
    steer_cap = MagicMock(side_effect=_SteeringQueueFull("queue full"))
    arbiter = StdinArbiter()
    stop_event = asyncio.Event()
    console = _make_console()

    call_count = 0

    def fake_ready(_timeout: float) -> bool:
        nonlocal call_count
        call_count += 1
        return call_count == 1

    async def stopper():
        # Wait until steer_cap is attempted, then stop
        while not steer_cap.called:
            await asyncio.sleep(0.005)
        stop_event.set()

    with (
        patch("amplifier_app_cli.main._stdin_ready", fake_ready),
        patch("sys.stdin") as mock_stdin,
    ):
        mock_stdin.readline.return_value = "do X\n"

        await asyncio.gather(
            asyncio.wait_for(
                _steering_reader(steer_cap, arbiter, stop_event, console),
                timeout=5.0,
            ),
            stopper(),
        )

    # steer_cap was called
    steer_cap.assert_called_once_with("do X")

    # A visible rejection message must be printed
    printed = [str(c) for c in console.print.call_args_list]
    assert any("Steering rejected" in msg for msg in printed), (
        f"Expected rejection message; got: {printed}"
    )
    # The ack must NOT be printed
    assert not any("queued" in msg for msg in printed)


# ---------------------------------------------------------------------------
# StdinArbiter unit tests (belt-and-suspenders)
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
# approval_provider arbiter integration (test begin/end called correctly)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approval_provider_sets_arbiter():
    """Approval provider sets arbiter.approval_active before and clears after request."""
    from amplifier_app_cli.approval_provider import CLIApprovalProvider
    from amplifier_core import ApprovalRequest

    arbiter = StdinArbiter()
    console = MagicMock()
    console.width = 80

    provider = CLIApprovalProvider(console, arbiter=arbiter)  # type: ignore[call-arg]

    # Track arbiter state during _do_request_approval
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
        assert not arbiter.approval_active  # cleared in finally

    # Was active during the inner call
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
