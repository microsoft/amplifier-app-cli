"""Integration tests: real pty + termios probing for the orphaned-prompt_task fix.

These tests exercise ``SteeringInputManager.run()`` inside a REAL forked pty
child process (not mocked event loop internals) and inspect the terminal's
actual ``termios`` state via the pty master fd after the child exits, to
prove -- at the OS level, not just at the asyncio-task level -- that no
orphaned ``prompt_task`` is left holding prompt_toolkit's ``raw_mode()``
terminal context after ``run()`` completes.

Adapted from the standalone investigation harness
(``investigate_termios_repro.py``, originally a workspace-root scratch
script) used to empirically confirm the root cause of the "terminal loses
echo after exiting an amplifier session" bug and to prove the fix in
``steering_input.py``'s ``run()`` (see the ``finally:`` block there).

Marked ``@pytest.mark.integration`` (deselected by default -- see
``pyproject.toml``'s ``addopts``) because each test forks a real process,
allocates a real pty pair, and does real termios syscalls; this is
meaningfully slower and heavier than the rest of the (mocked, in-process)
steering test suite in ``test_steering.py``. Run explicitly with::

    pytest -m integration tests/test_terminal_echo_integration.py

The headline scenario is ``multi_orphan``: four sequential steering "turns",
torn down back-to-back exactly the way ``_execute_with_interrupt`` does
(``stop_event.set(); reader_task.cancel(); await reader_task``, no yield in
between) with ZERO Ctrl-C and ZERO signals involved. Before the fix, this
scenario reliably corrupted the terminal (ECHO/ICANON/ISIG all cleared)
purely from asyncio's non-LIFO shutdown-sweep ordering of the orphaned
raw_mode contexts. After the fix, it comes out healthy every time.
"""

from __future__ import annotations

import os
import pty
import select
import signal
import sys
import termios
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parent.parent
LOGFILE = "/tmp/test_terminal_echo_integration.log"


def _describe_termios(attrs: list) -> dict[str, bool]:
    lflag = attrs[3]
    return {
        "ECHO": bool(lflag & termios.ECHO),
        "ICANON": bool(lflag & termios.ICANON),
        "ISIG": bool(lflag & termios.ISIG),
    }


def _is_healthy(state: dict) -> bool:
    return state.get("ECHO") is True and state.get("ICANON") is True


# ---------------------------------------------------------------------------
# Child process body -- runs the REAL SteeringInputManager inside the pty
# child, driving the exact task-creation/cancel/await pattern used by
# main.py's ``_execute_with_interrupt``.
# ---------------------------------------------------------------------------


def _child_main(scenario: str, ready_fd: int) -> None:
    import asyncio
    import time as _time

    sys.path.insert(0, str(REPO_ROOT))

    from prompt_toolkit.input import vt100 as pt_vt100
    from prompt_toolkit.patch_stdout import patch_stdout

    from amplifier_app_cli.steering_input import SteeringInputManager

    t0 = _time.monotonic()
    logfh = open(LOGFILE, "a")

    def log(msg: str) -> None:
        logfh.write(f"[child +{_time.monotonic() - t0:6.3f}s] {msg}\n")
        logfh.flush()

    class _NullConsole:
        def print(self, *a, **k):
            pass

    async def run_multi_orphan(n_turns: int) -> None:
        """Zero-Ctrl-C repro: N steering 'turns' back to back, each torn
        down the way ``_execute_with_interrupt`` does -- ``stop_event.set()``
        then immediately ``reader_task.cancel()``, no yield in between --
        guaranteed to land in the "unsafe" inner-loop sleep since the
        manager's task has had no chance to reach a safe cancellation
        checkpoint yet. NO Ctrl-C, NO real signals at all. Then exit
        normally and see if the accumulated orphaned raw_mode contexts
        (pre-fix) leave the terminal broken.
        """
        for i in range(n_turns):
            stop_event = asyncio.Event()

            class FakeArbiter:
                approval_active = False

            manager = SteeringInputManager(
                steer_cap=lambda text: None,
                arbiter=FakeArbiter(),
                stop_event=stop_event,
                console=_NullConsole(),
            )
            with patch_stdout(raw=True):
                reader_task = asyncio.create_task(manager.run())
                # Give run() just enough time to create prompt_task and reach
                # its inner polling sleep (the "unsafe" point), but not
                # enough to receive any input.
                await asyncio.sleep(0.08)
                log(f"turn {i}: tearing down (stop_event.set + cancel, no yield)")
                stop_event.set()
                reader_task.cancel()
                try:
                    await reader_task
                except asyncio.CancelledError:
                    pass
                pending = [
                    t for t in asyncio.all_tasks() if t is not asyncio.current_task()
                ]
                log(
                    f"turn {i}: tasks still pending immediately after teardown: {len(pending)}"
                )
            # Immediately move on to the "next prompt" -- no delay, no wait
            # for the orphan to clean up. Mirrors the REPL loop calling
            # prompt_session.prompt_async() again right after
            # _execute_with_interrupt() returns.
        log("all turns torn down; process about to exit normally (no Ctrl-C anywhere)")

    async def run_single_scenario() -> None:
        outer_session_stop = asyncio.Event()  # unused, placeholder parity with harness
        del outer_session_stop

        stop_event = asyncio.Event()

        class FakeArbiter:
            def __init__(self):
                self.approval_active = False

        arbiter = FakeArbiter()

        manager = SteeringInputManager(
            steer_cap=lambda text: None,
            arbiter=arbiter,
            stop_event=stop_event,
            console=_NullConsole(),
        )

        def sigint_handler(signum, frame):
            sys.stderr.write("[child] sigint_handler fired\n")
            sys.stderr.flush()

        original_handler = signal.signal(signal.SIGINT, sigint_handler)

        with patch_stdout(raw=True):
            reader_task = asyncio.create_task(manager.run())
            await asyncio.sleep(0.05)
            os.write(ready_fd, b"ready")
            os.close(ready_fd)

            if scenario == "normal":
                await asyncio.sleep(0.3)
            elif scenario == "ctrlc_midturn":
                await asyncio.sleep(0.5)
            elif scenario == "doublectrlc":
                await asyncio.sleep(0.6)
            elif scenario == "sigint_only":
                await asyncio.sleep(0.5)
            elif scenario == "bytes_only":
                await asyncio.sleep(0.5)
            elif scenario == "approval_cycle":
                await asyncio.sleep(0.15)
                arbiter.approval_active = True
                await asyncio.sleep(0.2)
                arbiter.approval_active = False
                await asyncio.sleep(0.2)

            tasks_before = {
                t for t in asyncio.all_tasks() if t is not asyncio.current_task()
            }
            log(f"tasks pending before teardown: {len(tasks_before)}")
            stop_event.set()
            reader_task.cancel()
            try:
                await reader_task
            except asyncio.CancelledError:
                pass

            await asyncio.sleep(0)
            leftover_tasks = [
                t for t in asyncio.all_tasks() if t is not asyncio.current_task()
            ]
            log(
                f"tasks pending AFTER reader_task awaited: {len(leftover_tasks)} -> {leftover_tasks}"
            )

        signal.signal(signal.SIGINT, original_handler)
        sys.stderr.write("[child] clean exit\n")
        sys.stderr.flush()
        log("run_single_scenario() about to return")

    # Instrument raw_mode enter/exit purely for the on-disk log (useful when
    # debugging a failure by hand); not asserted on directly by the tests.
    _orig_enter = pt_vt100.raw_mode.__enter__
    _orig_exit = pt_vt100.raw_mode.__exit__

    def _traced_enter(self):
        log(f"raw_mode.__enter__ (fd={self.fileno}, id={id(self)})")
        return _orig_enter(self)

    def _traced_exit(self, *a):
        log(f"raw_mode.__exit__  (fd={self.fileno}, id={id(self)})")
        return _orig_exit(self, *a)

    pt_vt100.raw_mode.__enter__ = _traced_enter
    pt_vt100.raw_mode.__exit__ = _traced_exit

    import asyncio as _asyncio

    try:
        if scenario == "multi_orphan":
            _asyncio.run(run_multi_orphan(4))
        else:
            _asyncio.run(run_single_scenario())
    except BaseException as e:  # noqa: BLE001
        sys.stderr.write(f"[child] asyncio.run raised: {type(e).__name__}: {e}\n")
        sys.stderr.flush()
        raise
    sys.stderr.write("[child] process exiting normally\n")
    sys.stderr.flush()


# ---------------------------------------------------------------------------
# Parent-side driver: forks the pty child, optionally injects bytes/signals,
# waits for exit, then reads back termios state via the master fd.
# ---------------------------------------------------------------------------


def _run_pty_scenario(scenario: str, timeout: float = 8.0) -> dict:
    ready_read_fd, ready_write_fd = os.pipe()
    pid, master_fd = pty.fork()
    if pid == 0:
        # ----- CHILD -----
        os.close(ready_read_fd)
        try:
            _child_main(scenario, ready_write_fd)
        except BaseException:
            os._exit(1)
        os._exit(0)

    # ----- PARENT -----
    os.close(ready_write_fd)

    def send(text: str) -> None:
        os.write(master_fd, text.encode())

    if scenario != "multi_orphan":
        readable, _, _ = select.select([ready_read_fd], [], [], 2.0)
        ready = os.read(ready_read_fd, 5) if readable else b""
        if ready != b"ready":
            try:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            except ProcessLookupError:
                pass
            os.close(ready_read_fd)
            os.close(master_fd)
            raise AssertionError(f"PTY child did not signal input readiness: {ready!r}")
    os.close(ready_read_fd)

    if scenario == "ctrlc_midturn":
        send("\x03")  # Ctrl-C byte (prompt_toolkit key path)
    elif scenario == "doublectrlc":
        send("\x03")
        time.sleep(0.05)
        send("\x03")
        try:
            os.kill(pid, signal.SIGINT)  # real OS signal racing the byte
        except ProcessLookupError:
            pass
    elif scenario == "sigint_only":
        try:
            os.kill(pid, signal.SIGINT)
        except ProcessLookupError:
            pass
    elif scenario == "bytes_only":
        send("\x03")
        time.sleep(0.05)
        send("\x03")
    # "normal", "approval_cycle", "multi_orphan": driven entirely by the
    # child's own internal timers -- no parent-side input needed.

    deadline = time.time() + timeout
    exited = False
    status = None
    while time.time() < deadline:
        try:
            wpid, status = os.waitpid(pid, os.WNOHANG)
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

    # Drain any remaining child output (avoids leaving data in the pty buffer).
    output = b""
    try:
        os.set_blocking(master_fd, False)
        for _ in range(20):
            try:
                chunk = os.read(master_fd, 65536)
            except (BlockingIOError, OSError):
                break
            if not chunk:
                break
            output += chunk
    except OSError:
        pass

    try:
        attrs = termios.tcgetattr(master_fd)
        state = _describe_termios(attrs)
    except OSError as e:
        state = {"error": str(e)}

    os.close(master_fd)

    return {
        "scenario": scenario,
        "exit_status": status,
        "exited_cleanly": exited,
        "termios": state,
        "healthy": _is_healthy(state),
        "output": output.decode(errors="replace"),
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_multi_orphan_leaves_terminal_healthy():
    """Headline proof: 4 sequential turns, zero Ctrl-C, zero signals, only
    the standard ``stop_event.set(); cancel(); await`` teardown run 4 times
    back-to-back. Before the fix in ``steering_input.py``'s ``run()``
    ``finally:`` block, this reliably corrupted the terminal (ECHO/ICANON/
    ISIG all cleared) after a completely normal process exit, because the
    orphaned child ``prompt_task``s' ``raw_mode`` contexts were swept in a
    non-LIFO order by asyncio's shutdown machinery. After the fix, the
    terminal must come out healthy every time.
    """
    result = _run_pty_scenario("multi_orphan")
    assert result["exited_cleanly"], f"child did not exit cleanly: {result}"
    assert result["healthy"], f"terminal corrupted after multi_orphan: {result}"


@pytest.mark.parametrize(
    "scenario",
    [
        "normal",
        "ctrlc_midturn",
        "doublectrlc",
        "sigint_only",
        "bytes_only",
        "approval_cycle",
    ],
)
def test_scenario_leaves_terminal_healthy(scenario: str):
    """Regression coverage for the other empirically-tested teardown paths.

    None of these scenarios are known to corrupt the terminal after the
    orphan-prevention fix; this guards against a future regression
    reintroducing an orphaned raw_mode context on any of these paths.
    """
    result = _run_pty_scenario(scenario)
    assert result["exited_cleanly"], f"child did not exit cleanly: {result}"
    assert result["healthy"], f"terminal corrupted after {scenario}: {result}"
