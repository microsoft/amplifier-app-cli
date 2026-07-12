"""Integration tests: real pty pair (deliberately un-drained) proving the
CONFIRMED event-loop-freeze mechanism in ``prompt_toolkit.patch_stdout`` and
proving our fix (``amplifier_app_cli.stdout_offload.patch_stdout_offloaded``)
genuinely isolates the block to a worker thread instead of the asyncio event
loop.

Background
~~~~~~~~~~
``prompt_toolkit.patch_stdout.StdoutProxy._write_and_flush()`` hardcodes::

    run_in_terminal(write_and_flush, in_executor=False)

(``prompt_toolkit/patch_stdout.py`` -- see ``amplifier_app_cli/stdout_offload.py``
for the full mechanism writeup). ``in_executor=False`` means the actual
OS-level terminal write executes SYNCHRONOUSLY on the asyncio event-loop
thread. If that write blocks -- because the terminal consumer (a busy or
backgrounded tmux pane, a slow SSH link) isn't draining its pty fast enough --
the ENTIRE event loop freezes: no other task, timer, or callback can run at
all until the write returns. This explained every prior "hangs until Enter"
incident across a real production investigation, including two live sessions
with concurrent delegation.

Reproduction strategy
~~~~~~~~~~~~~~~~~~~~~
Each scenario forks a child process that:

1. Opens a real pty pair and NEVER reads the master side (simulating a
   busy/backgrounded pane under load -- the exact real-world condition).
2. Builds a real ``prompt_toolkit.output.vt100.Vt100_Output`` writing to the
   pty's slave side, and installs it as the ambient
   ``prompt_toolkit.application.current`` AppSession's output.
3. Installs a minimal stand-in "app" object (just ``.loop`` +
   ``._is_running``) as the AppSession's active app, so
   ``StdoutProxy._get_app_loop()`` returns a real loop (matching a genuine
   interactive session) instead of ``None``.
4. Runs a 50ms-interval "ticker" task recording wall-clock timestamps --
   our probe for event-loop liveness.
5. After a short warm-up (during which the ticker should already show
   several ticks), writes ~4MB through ``sys.stdout`` (comfortably larger
   than any kernel pty buffer) inside either the UNFIXED
   ``prompt_toolkit.patch_stdout.patch_stdout(raw=True)`` or the FIXED
   ``amplifier_app_cli.stdout_offload.patch_stdout_offloaded(raw=True)``.
6. Observes for ~2.5 more seconds, then writes the ticker's recorded
   timestamps to a result file and hard-exits via ``os._exit(0)`` (skipping
   asyncio/atexit cleanup, since the huge write may still be permanently
   stuck in a background thread -- we never drain the pty at all in this
   test, on purpose, to keep the reproduction unambiguous).

If the event loop freezes (unfixed path), the child never reaches step 6 at
all -- the whole process hangs solid, exactly as confirmed in production, and
the parent test must kill it via an external timeout. If the event loop
stays responsive (fixed path), the child reaches step 6 promptly and the
ticker shows continuous, small-gap progress throughout.

Marked ``@pytest.mark.integration`` per this repo's convention for tests that
fork a real process and allocate a real pty (deselected by default). Run
explicitly with::

    pytest -m integration tests/test_stdout_offload_freeze_integration.py
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parent.parent

# ~4MB comfortably overruns a kernel pty buffer (typically a few KB to
# ~64KB), guaranteeing the underlying flush() blocks since nobody reads it.
WRITE_SIZE_BYTES = 4 * 1024 * 1024
WARMUP_S = 0.3
OBSERVE_S = 2.5
TICKER_INTERVAL_S = 0.05


def _child_main(fixed: bool, result_path: str) -> None:
    """Runs inside the forked child. See module docstring for the full
    reproduction strategy. Ends by hard-exiting via ``os._exit()`` --
    reached ONLY if the event loop never froze (see docstring point 6).
    """
    import asyncio

    from prompt_toolkit.application.current import create_app_session
    from prompt_toolkit.output.vt100 import Vt100_Output

    # Real pty pair. Deliberately never read `target_master_fd` -- this is
    # the "busy/backgrounded tmux pane" condition that never drains.
    target_master_fd, target_slave_fd = os.openpty()
    del target_master_fd  # intentionally never read from -- see docstring

    target_slave_file = os.fdopen(target_slave_fd, "w", encoding="utf-8", closefd=True)
    output = Vt100_Output.from_pty(target_slave_file)

    class _FakeApp:
        """Minimal stand-in for a running prompt_toolkit Application.

        ``StdoutProxy._get_app_loop()`` only reads ``.loop``; the confirmed
        mechanism depends on that being a real, non-None loop (matching a
        genuine interactive session) so writes are dispatched via
        ``loop.call_soon_threadsafe(...)`` instead of being written
        directly on the background write-thread. ``._is_running = False``
        deliberately takes ``run_in_terminal``'s ``in_terminal()`` fast
        short-circuit path (no real Renderer/Layout needed) -- that path
        is unaffected by the fix under test, which only changes the
        ``in_executor`` flag passed to ``run_in_terminal``.
        """

        def __init__(self, loop: "asyncio.AbstractEventLoop") -> None:
            self.loop = loop
            self._is_running = False

    ticks: list[float] = []

    async def ticker() -> None:
        while True:
            ticks.append(time.monotonic())
            await asyncio.sleep(TICKER_INTERVAL_S)

    async def scenario() -> None:
        with create_app_session(output=output) as app_session:
            app_session.app = _FakeApp(asyncio.get_running_loop())  # type: ignore[assignment]

            if fixed:
                from amplifier_app_cli.stdout_offload import (
                    patch_stdout_offloaded as patcher,
                )
            else:
                from prompt_toolkit.patch_stdout import patch_stdout as patcher

            with patcher(raw=True):
                ticker_task = asyncio.create_task(ticker())
                await asyncio.sleep(WARMUP_S)

                big_payload = "x" * WRITE_SIZE_BYTES
                sys.stdout.write(big_payload)
                sys.stdout.flush()

                await asyncio.sleep(OBSERVE_S)
                ticker_task.cancel()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    # NOTE: intentionally `run_until_complete`, not `asyncio.run()`. If the
    # write was offloaded (fixed path), the executor future backing that
    # write never resolves (we never drain the pty) -- `asyncio.run()`'s
    # own shutdown machinery would hang trying to cancel/join that stray
    # task. `run_until_complete` only waits on `scenario()` itself, which
    # returns without awaiting that stray task.
    loop.run_until_complete(scenario())

    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(ticks, f)

    # Hard-exit: skip asyncio/atexit cleanup entirely (see note above --
    # a background thread may be permanently blocked on the un-drained pty).
    os._exit(0)


def _run_scenario(fixed: bool, timeout: float = 6.0) -> tuple[bool, list[float] | None]:
    """Forks a child running ``_child_main``. Returns
    ``(exited_on_its_own, ticks_or_None)``.
    """
    tag = "fixed" if fixed else "unfixed"
    result_path = f"/tmp/test_stdout_offload_freeze_{tag}_{os.getpid()}.json"
    if os.path.exists(result_path):
        os.remove(result_path)

    pid = os.fork()
    if pid == 0:
        sys.path.insert(0, str(REPO_ROOT))
        try:
            _child_main(fixed, result_path)
        except BaseException:
            os._exit(1)
        os._exit(0)

    deadline = time.time() + timeout
    exited = False
    while time.time() < deadline:
        wpid, _status = os.waitpid(pid, os.WNOHANG)
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

    ticks: list[float] | None = None
    if os.path.exists(result_path):
        ticks = json.loads(Path(result_path).read_text(encoding="utf-8"))
        os.remove(result_path)

    return exited, ticks


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_unfixed_patch_stdout_wedges_event_loop_under_pty_backpressure():
    """RED: reproduces the CONFIRMED production mechanism directly.

    prompt_toolkit's stock ``patch_stdout(raw=True)`` hardcodes
    ``in_executor=False``. Under un-drained pty backpressure, the blocking
    OS write executes synchronously on the event-loop thread and wedges the
    ENTIRE loop solid -- not merely delays it. The child process cannot
    even reach its own result-writing code, matching the confirmed
    production observation exactly ("the entire Python process froze
    solid... had to be killed externally... total silence = total freeze").
    """
    exited, ticks = _run_scenario(fixed=False, timeout=6.0)

    assert not exited, (
        "Expected the unfixed prompt_toolkit patch_stdout() path to wedge "
        "the event loop completely under un-drained pty backpressure -- "
        "the child should require an external kill. If it exited on its "
        "own, either the bug is already fixed upstream or the repro "
        "conditions need review."
    )
    assert ticks is None, (
        "Process should never have reached the result-writing code -- the "
        "event loop was supposed to be completely wedged before that point."
    )


def test_fixed_patch_stdout_offloaded_stays_responsive_under_pty_backpressure():
    """GREEN: with the fix (``patch_stdout_offloaded``), the identical
    backpressure condition does NOT freeze the event loop -- concurrent
    async work (the ticker task) keeps completing on schedule because the
    actual blocking OS write is offloaded to a worker thread.
    """
    exited, ticks = _run_scenario(fixed=True, timeout=6.0)

    assert exited, (
        "Fixed path should exit on its own -- it should never need to be killed."
    )
    assert ticks is not None, (
        "Fixed path should have written tick results before exiting."
    )

    expected_min_ticks = int((WARMUP_S + OBSERVE_S) / TICKER_INTERVAL_S * 0.5)
    assert len(ticks) >= expected_min_ticks, (
        f"Expected steady ticking throughout the ~{WARMUP_S + OBSERVE_S:.1f}s "
        f"run (>= {expected_min_ticks} ticks), got {len(ticks)}: {ticks}"
    )

    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    max_gap = max(gaps)
    assert max_gap < 0.5, (
        f"Ticker task stalled for {max_gap:.2f}s while the large write was "
        "in flight -- the event loop was not responsive; offload is not "
        f"working. All gaps: {gaps}"
    )
