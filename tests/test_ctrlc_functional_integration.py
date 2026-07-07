"""Integration tests: real pty + real CancellationToken proof that a physical
Ctrl-C keypress during a mid-turn steering prompt actually cancels the turn.

Background
~~~~~~~~~~
A physical Ctrl-C keypress during an active turn does NOT generate a real OS
``SIGINT`` while the steering prompt holds ``prompt_toolkit``'s
``raw_mode()`` -- ``raw_mode()`` clears ``ISIG`` along with ``ECHO``/
``ICANON`` on the tty (``prompt_toolkit.input.vt100.raw_mode._patch_lflag``),
so the ``\\x03`` byte never becomes a kernel-delivered ``SIGINT`` while this
prompt has focus. It is consumed entirely by the steering ``PromptSession``'s
``interrupt_exception=_CtrlCInterrupt`` key binding and caught by
``SteeringInputManager.run()``'s ``except _CtrlCInterrupt`` clause.

Before the fix, that clause silently ``continue``d: no console feedback, and
``session.coordinator.cancellation`` was never touched -- a real turn ran to
full completion as if Ctrl-C had never been pressed (confirmed both via this
harness and live in a running CLI session).

The fix (``SteeringInputManager._handle_ctrl_c()``) forwards a
``_CtrlCInterrupt`` to ``self._session.coordinator.cancellation`` with the
SAME graceful-then-immediate escalation and the SAME visible console
messages as ``main.py``'s OS-signal ``sigint_handler`` in
``_execute_with_interrupt``. Because both paths mutate the identical shared
``CancellationToken`` object that ``_execute_with_interrupt``'s own poll loop
reads (``cancellation.is_immediate``) to decide whether to force-cancel the
running ``execute_task``, the downstream effect is identical regardless of
which path caught the keypress.

These tests run the REAL ``SteeringInputManager`` and the REAL
``amplifier_core.cancellation.CancellationToken`` inside a real forked pty
child process (not mocked event-loop internals), driving a long-running fake
"execute_task" (standing in for ``session.execute()``) through the exact
poll-loop pattern ``_execute_with_interrupt`` uses, and assert on the
FUNCTIONAL outcome: did the cancellation token's state change, was the
running task actually force-cancelled, how long did it take -- not just
termios state (that is covered separately by
``test_terminal_echo_integration.py``).

Marked ``@pytest.mark.integration`` for the same reason as
``test_terminal_echo_integration.py``: forks a real process and allocates a
real pty pair. Run explicitly with::

    pytest -m integration tests/test_ctrlc_functional_integration.py
"""

from __future__ import annotations

import json
import os
import pty
import signal
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parent.parent

# Long enough that "ran to completion untouched" and "force-cancelled early"
# are unambiguous outcomes at the timing granularities used below.
FAKE_TASK_DURATION = 3.0


def _child_main(scenario: str, result_path: str, ready_path: str) -> None:
    """Runs inside the forked pty child. Mirrors main.py's
    ``_execute_with_interrupt`` structure with a fake long-running
    execute_task standing in for ``session.execute()``, and writes a JSON
    result describing the functional outcome to ``result_path`` before
    exiting.

    Writes ``ready_path`` the instant ``raw_mode()`` is actually entered
    (prompt_toolkit's ``Application.run_async()``, called from the
    steering prompt's ``prompt_async()``) -- this is the deterministic
    readiness signal the parent waits on before sending any keystrokes,
    instead of a fixed sleep. This matters here specifically (unlike the
    termios-only tests) because sending the ``\\x03`` byte too early --
    before ``raw_mode()`` has cleared ``ISIG`` -- delivers a REAL kernel
    SIGINT instead of the raw byte, which would be caught by this script's
    OWN bare ``sigint_handler`` mirror (no console message) rather than by
    ``SteeringInputManager``'s ``_CtrlCInterrupt`` path (which prints a
    message) -- silently testing the wrong code path and flaking under
    slower startup conditions (e.g. running under pytest's import/collection
    overhead vs. a bare script).
    """
    # Re-derive sys.stdin/stdout/stderr from the raw fds BEFORE any other
    # imports. This process was created via ``pty.fork()``, which connects
    # fds 0/1/2 to the pty slave at the OS level -- but the forked child's
    # Python-level ``sys.stdin``/``sys.stdout``/``sys.stderr`` objects are
    # inherited by value from the parent (this test) process's memory at
    # fork time. Under a test runner that captures/replaces those objects
    # (e.g. pytest's default output capturing), the inherited objects do
    # NOT necessarily behave like a real terminal stream even though the
    # underlying fds are correct, which can cause prompt_toolkit's
    # tty-detection/raw-mode setup to behave differently than it would in
    # a bare script invocation. Rebuilding them from the raw fds makes this
    # child's behavior deterministic regardless of the parent's I/O
    # capturing configuration.
    sys.stdin = os.fdopen(0, "r", closefd=False)
    sys.stdout = os.fdopen(1, "w", closefd=False)
    sys.stderr = os.fdopen(2, "w", closefd=False)

    import asyncio

    from amplifier_core.cancellation import CancellationToken
    from prompt_toolkit.input import vt100 as pt_vt100
    from prompt_toolkit.patch_stdout import patch_stdout

    from amplifier_app_cli.steering_input import SteeringInputManager

    _orig_raw_enter = pt_vt100.raw_mode.__enter__

    def _traced_raw_enter(self):
        result = _orig_raw_enter(self)
        Path(ready_path).write_text("ready")
        return result

    pt_vt100.raw_mode.__enter__ = _traced_raw_enter

    messages: list[str] = []

    class _RecordingConsole:
        def print(self, *a, **_k):
            if a:
                messages.append(str(a[0]))

    class _FakeCoordinator:
        def __init__(self, cancellation_token):
            self.cancellation = cancellation_token

    class _FakeSession:
        """Exposes only what SteeringInputManager needs:
        ``.coordinator.cancellation`` -- the SAME CancellationToken instance
        main.py's own execute-task poll loop would read in production.
        """

        def __init__(self, cancellation_token):
            self.coordinator = _FakeCoordinator(cancellation_token)

    async def fake_execute_task(duration: float) -> str:
        """Stands in for ``session.execute(prompt_text)``: a single
        long-running coroutine with no cancellation-token polling of its
        own (that's the orchestrator's job in the real system) -- the only
        thing that can stop it early is asyncio-level `.cancel()` from the
        OUTER poll loop below, mirroring main.py's
        ``_execute_with_interrupt`` (~lines 2868-2873).
        """
        await asyncio.sleep(duration)
        return "fake response"

    async def run_scenario() -> dict:
        cancellation = CancellationToken()

        def sigint_handler(signum, frame):
            """Verbatim mirror of main.py's sigint_handler -- exercises the
            OS-signal path for scenarios that send a real SIGINT.
            """
            if cancellation.is_cancelled:
                cancellation.request_immediate()
            else:
                cancellation.request_graceful()

        original_handler = signal.signal(signal.SIGINT, sigint_handler)

        stop_event = asyncio.Event()

        class _FakeArbiter:
            approval_active = False

        manager = SteeringInputManager(
            steer_cap=lambda text: None,
            arbiter=_FakeArbiter(),
            stop_event=stop_event,
            console=_RecordingConsole(),
            session=_FakeSession(cancellation),
        )

        t_start = asyncio.get_event_loop().time()
        task_cancelled = False
        with patch_stdout(raw=True):
            reader_task = asyncio.create_task(manager.run())
            try:
                execute_task = asyncio.create_task(
                    fake_execute_task(FAKE_TASK_DURATION)
                )

                # Verbatim mirror of main.py's poll loop: ONLY checks
                # is_immediate, every 50ms.
                while not execute_task.done():
                    if cancellation.is_immediate:
                        execute_task.cancel()
                        task_cancelled = True
                        break
                    await asyncio.sleep(0.05)

                try:
                    await execute_task
                except asyncio.CancelledError:
                    task_cancelled = True
            finally:
                stop_event.set()
                reader_task.cancel()
                try:
                    await reader_task
                except asyncio.CancelledError:
                    pass

        elapsed = asyncio.get_event_loop().time() - t_start
        signal.signal(signal.SIGINT, original_handler)

        return {
            "scenario": scenario,
            "elapsed": elapsed,
            "state": cancellation.state,
            "is_cancelled": cancellation.is_cancelled,
            "is_immediate": cancellation.is_immediate,
            "task_cancelled": task_cancelled,
            "messages": messages,
        }

    result = asyncio.run(run_scenario())
    Path(result_path).write_text(json.dumps(result))


def _run_pty_scenario(scenario: str, timeout: float = 8.0) -> dict:
    result_path = f"/tmp/test_ctrlc_functional_{scenario}_{os.getpid()}.json"
    ready_path = f"/tmp/test_ctrlc_functional_ready_{scenario}_{os.getpid()}.marker"
    for p in (result_path, ready_path):
        if os.path.exists(p):
            os.remove(p)

    pid, master_fd = pty.fork()
    if pid == 0:
        sys.path.insert(0, str(REPO_ROOT))
        try:
            _child_main(scenario, result_path, ready_path)
        except BaseException:
            os._exit(1)
        os._exit(0)

    # Wait for the deterministic readiness signal (raw_mode actually
    # entered) instead of a fixed sleep -- see _child_main's docstring for
    # why sending keystrokes before raw_mode clears ISIG would silently
    # test the wrong code path (a real kernel SIGINT instead of the raw
    # byte).
    ready_deadline = time.time() + 5.0
    while not os.path.exists(ready_path):
        if time.time() > ready_deadline:
            raise TimeoutError(
                f"child never signaled raw_mode readiness for scenario={scenario!r}"
            )
        time.sleep(0.01)
    # Tiny settle margin: raw_mode.__enter__ has returned, but give
    # prompt_toolkit's own input-reader registration a moment to actually
    # start polling the fd before we write to it.
    time.sleep(0.05)

    def send(text: str) -> None:
        os.write(master_fd, text.encode())

    if scenario == "single_ctrlc":
        # One physical \x03 keypress, NO real OS signal at all -- the exact
        # keystroke a user's terminal sends while raw_mode() has ISIG off.
        send("\x03")
    elif scenario == "double_ctrlc":
        # Two keypresses, NO real signal -- proves the keystroke-only path
        # escalates graceful -> immediate on its own.
        send("\x03")
        time.sleep(0.1)
        send("\x03")
    elif scenario == "typing_then_ctrlc":
        # Ctrl-C arrives mid-compose of a steer message.
        send("hello wor")
        time.sleep(0.1)
        send("\x03")
    elif scenario == "sigint_only":
        # Regression: the pre-existing real-OS-signal path must still work.
        try:
            os.kill(pid, signal.SIGINT)
        except ProcessLookupError:
            pass

    deadline = time.time() + timeout
    exited = False
    while time.time() < deadline:
        try:
            wpid, _status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            exited = True
            break
        if wpid == pid:
            exited = True
            break
        time.sleep(0.05)

    if not exited:
        try:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        except ProcessLookupError:
            pass

    os.set_blocking(master_fd, False)
    try:
        for _ in range(20):
            try:
                chunk = os.read(master_fd, 65536)
            except (BlockingIOError, OSError):
                break
            if not chunk:
                break
    except OSError:
        pass
    os.close(master_fd)

    if not os.path.exists(result_path):
        return {
            "scenario": scenario,
            "exited_cleanly": False,
            "error": "no result file written",
        }

    result = json.loads(Path(result_path).read_text())
    result["exited_cleanly"] = exited
    os.remove(result_path)
    return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_single_ctrlc_requests_graceful_cancellation():
    """Headline proof (part 1): ONE physical Ctrl-C keypress during a turn,
    with NO OS signal involved at all, must move
    session.coordinator.cancellation to the graceful state and print the
    same feedback sigint_handler prints on a first Ctrl-C.

    Before the fix: state stayed 'none', is_cancelled was False, and the
    fake task ran to full completion untouched (confirmed via this same
    scenario in the prior investigation pass).
    """
    result = _run_pty_scenario("single_ctrlc")
    assert result.get("exited_cleanly"), f"child did not exit cleanly: {result}"
    assert result["state"] == "graceful", (
        f"expected graceful cancellation, got: {result}"
    )
    assert result["is_cancelled"] is True
    assert any("Stopping after current operation" in m for m in result["messages"]), (
        f"expected sigint_handler's first-Ctrl-C message, got messages={result['messages']!r}"
    )
    # A single Ctrl-C only requests graceful -- matches production semantics
    # exactly (only a SECOND Ctrl-C forces is_immediate, which is what the
    # execute-task poll loop actually checks) -- so the fake task should run
    # to (approximately) full completion, not be force-cancelled early.
    assert result["task_cancelled"] is False
    assert result["elapsed"] >= FAKE_TASK_DURATION * 0.9


def test_double_ctrlc_requests_immediate_cancellation_and_stops_the_task():
    """Headline proof (part 2): TWO physical Ctrl-C keypresses, still with
    NO OS signal at all, must escalate to immediate cancellation AND
    actually force-cancel the running task -- the identical downstream
    effect a real double OS SIGINT produces today.
    """
    result = _run_pty_scenario("double_ctrlc")
    assert result.get("exited_cleanly"), f"child did not exit cleanly: {result}"
    assert result["state"] == "immediate", (
        f"expected immediate cancellation, got: {result}"
    )
    assert result["is_cancelled"] is True
    assert result["is_immediate"] is True
    assert any("Cancelling immediately" in m for m in result["messages"]), (
        f"expected sigint_handler's second-Ctrl-C message, got messages={result['messages']!r}"
    )
    # The task must have actually been force-cancelled, not merely flagged.
    assert result["task_cancelled"] is True, (
        f"expected the fake task to be force-cancelled: {result}"
    )
    assert result["elapsed"] < FAKE_TASK_DURATION * 0.9, (
        f"expected the task to stop well before its full duration: {result}"
    )


def test_ctrlc_mid_compose_still_forwards_to_cancellation():
    """A Ctrl-C that interrupts an in-progress steer compose (user was
    mid-typing) must still forward to cancellation -- not be absorbed by
    the compose buffer.
    """
    result = _run_pty_scenario("typing_then_ctrlc")
    assert result.get("exited_cleanly"), f"child did not exit cleanly: {result}"
    assert result["state"] == "graceful", (
        f"expected graceful cancellation, got: {result}"
    )
    assert result["is_cancelled"] is True


def test_sigint_only_regression_still_works():
    """Regression: the pre-existing real-OS-SIGINT path (main.py's
    sigint_handler, exercised outside the steering prompt's raw_mode focus
    window) must be unaffected by this fix.
    """
    result = _run_pty_scenario("sigint_only")
    assert result.get("exited_cleanly"), f"child did not exit cleanly: {result}"
    assert result["state"] == "graceful", (
        f"expected graceful cancellation, got: {result}"
    )
    assert result["is_cancelled"] is True
