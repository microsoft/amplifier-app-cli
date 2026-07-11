"""Regression tests for a main-thread stdin-block bug in the REPL loop.

Background
~~~~~~~~~~
A real Amplifier CLI session was found stuck (hours of no progress).
Direct ``/proc/<pid>/wchan`` inspection of the MAIN thread (not a
background worker) showed ``wait_woken`` -- the canonical/line-buffered
blocking-read signature (a real ``read()`` syscall waiting for a full
line terminated by Enter), with zero child processes running. A single
Enter keystroke sent to the pty immediately released the thread (wchan
transitioned to ``ep_poll``, the healthy asyncio-epoll-wait state) and
previously-stalled output resumed.

Root cause: ``interactive_chat()``'s REPL loop in ``main.py`` calls
``click.confirm("Exit Amplifier?", default=False)`` directly inside its
``except KeyboardInterrupt:`` clause. ``click.confirm`` performs a
synchronous, canonical-mode blocking read (``input()``) with **no**
executor/thread offload. Because ``interactive_chat`` is an ``async def``
coroutine driven by a single-threaded asyncio event loop, calling a bare
blocking function directly inside it freezes the ENTIRE event loop thread
-- no other task, callback, or I/O event (including a delegated
sub-agent's in-flight async work) can make progress until the blocking
read returns.

This is the exact anti-pattern the codebase already knows to avoid
elsewhere:
  * ``approval_provider.py``'s ``_get_user_input()`` wraps
    ``Confirm.ask(...)`` in ``loop.run_in_executor(None, ...)``.
  * ``ui/approval.py``'s ``request_approval()`` wraps ``Prompt.ask(...)``
    in ``asyncio.to_thread(...)``.

The fix mirrors those: offload ``click.confirm`` via ``asyncio.to_thread``
so the blocking read runs on a worker thread, never the main event-loop
thread.

Two tests below:

1. ``test_repl_exit_confirmation_not_called_directly`` -- a precise,
   AST-based structural test targeting the exact bug location
   (``interactive_chat``'s ``except KeyboardInterrupt`` handler in
   ``amplifier_app_cli/main.py``). Fails against the original code (a
   bare ``click.confirm(...)`` call is present) and passes once the call
   is offloaded via ``asyncio.to_thread``.

2. ``test_synchronous_blocking_call_freezes_event_loop`` /
   ``test_thread_offloaded_blocking_call_does_not_freeze_event_loop`` --
   a dynamic mechanism reproduction (independent of main.py's own
   complexity) that empirically proves a bare blocking call inside a
   coroutine starves a concurrently scheduled asyncio task, while the
   same call wrapped in ``asyncio.to_thread`` does not.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
import time

import pytest


# ---------------------------------------------------------------------------
# Test 1: exact bug location, AST-based structural assertion
# ---------------------------------------------------------------------------


def _direct_calls_matching(tree: ast.AST, module_name: str, attr_name: str) -> list[ast.Call]:
    """Return ``ast.Call`` nodes whose function is ``module_name.attr_name(...)``.

    A call passed BY REFERENCE to ``asyncio.to_thread(click.confirm, ...)``
    or ``loop.run_in_executor(None, click.confirm, ...)`` does NOT show up
    here -- in that form ``click.confirm`` is an ``ast.Attribute`` used as
    a plain argument value, never itself the ``func`` of a ``Call`` node.
    Only a *direct* invocation like ``click.confirm(...)`` matches.
    """
    matches = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == attr_name
                and isinstance(func.value, ast.Name)
                and func.value.id == module_name
            ):
                matches.append(node)
    return matches


def test_repl_exit_confirmation_not_called_directly_on_main_thread():
    """``click.confirm()`` must never be called directly inside
    ``interactive_chat()`` -- it must be offloaded to a worker thread.

    Regression test for the main-thread stdin-block bug confirmed via live
    ``/proc/<pid>/wchan`` inspection of a stuck production session (see
    module docstring).
    """
    import importlib

    # NOTE: `amplifier_app_cli/__init__.py` does `from .main import main`,
    # which shadows the `main` submodule name with the CLI entry-point
    # function in the package namespace. `import amplifier_app_cli.main`
    # (rather than `from amplifier_app_cli import main`) is required to
    # reach the actual module.
    main = importlib.import_module("amplifier_app_cli.main")

    source = inspect.getsource(main.interactive_chat)
    tree = ast.parse(textwrap.dedent(source))

    direct_calls = _direct_calls_matching(tree, "click", "confirm")

    assert not direct_calls, (
        "click.confirm() is called directly inside interactive_chat() "
        "(found on source line offset "
        f"{[c.lineno for c in direct_calls]} relative to the function). "
        "This performs a synchronous, canonical-mode blocking stdin read "
        "on the asyncio event loop's MAIN thread, freezing the entire "
        "event loop (including any in-flight delegated sub-agent work) "
        "until Enter is pressed. Wrap it with "
        "`await asyncio.to_thread(click.confirm, ...)` -- mirroring the "
        "existing correct pattern in approval_provider.py and "
        "ui/approval.py."
    )


# ---------------------------------------------------------------------------
# Test 2: dynamic mechanism reproduction (independent of main.py wiring)
# ---------------------------------------------------------------------------
#
# Mirrors the exact shape of the buggy code:
#
#     except KeyboardInterrupt:
#         if click.confirm("Exit Amplifier?", default=False):
#             ...
#
# `_blocking_stdin_read` stands in for click.confirm's underlying `input()`
# call: a real, synchronous, thread-blocking (time.sleep) operation with no
# yield point back to the event loop.


def _blocking_stdin_read(delay: float, marker: dict) -> bool:
    """Stand-in for click.confirm()'s underlying input() -- a genuinely
    synchronous, thread-blocking call with no asyncio yield point."""
    marker["confirm_started_at"] = time.monotonic()
    time.sleep(delay)  # the canonical, line-buffered blocking read
    marker["confirm_finished_at"] = time.monotonic()
    return False


async def _buggy_keyboard_interrupt_handler(delay: float, marker: dict) -> None:
    """Verbatim shape of the bug: a blocking call invoked directly inside
    an async coroutine's exception handler -- no executor offload."""
    try:
        raise KeyboardInterrupt
    except KeyboardInterrupt:
        _blocking_stdin_read(delay, marker)  # BUG: blocks the main thread


async def _fixed_keyboard_interrupt_handler(delay: float, marker: dict) -> None:
    """Fixed shape: same call, offloaded via asyncio.to_thread."""
    import asyncio

    try:
        raise KeyboardInterrupt
    except KeyboardInterrupt:
        await asyncio.to_thread(_blocking_stdin_read, delay, marker)


async def _in_flight_delegated_work(marker: dict) -> None:
    """Represents a delegated sub-agent's already-scheduled async task that
    should be able to make progress concurrently with anything else the
    event loop is doing."""
    import asyncio

    await asyncio.sleep(0.05)
    marker["delegated_work_completed_at"] = time.monotonic()


@pytest.mark.asyncio
async def test_synchronous_blocking_call_freezes_event_loop():
    """RED-proving mechanism test: a bare blocking call inside a coroutine
    starves every other concurrently scheduled asyncio task until it
    returns -- exactly the wchan==wait_woken freeze observed live."""
    import asyncio

    marker: dict = {}
    delay = 0.3  # long enough that ordering is unambiguous

    delegated_task = asyncio.create_task(_in_flight_delegated_work(marker))
    await _buggy_keyboard_interrupt_handler(delay, marker)
    await delegated_task

    # Proof of the freeze: the delegated task could not complete until
    # AFTER the blocking confirm() call returned, even though it only
    # needed 0.05s and was scheduled before the 0.3s blocking call began.
    assert marker["delegated_work_completed_at"] >= marker["confirm_finished_at"], (
        "Expected the in-flight delegated task to be starved until the "
        "blocking call released the event loop, but it completed before "
        "the blocking call finished -- the reproduction no longer "
        "demonstrates the freeze."
    )


@pytest.mark.asyncio
async def test_thread_offloaded_blocking_call_does_not_freeze_event_loop():
    """GREEN-proving mechanism test: offloading the identical blocking call
    via asyncio.to_thread lets concurrently scheduled work proceed while
    the blocking read waits on a worker thread."""
    import asyncio

    marker: dict = {}
    delay = 0.3

    delegated_task = asyncio.create_task(_in_flight_delegated_work(marker))
    await _fixed_keyboard_interrupt_handler(delay, marker)
    await delegated_task

    # Proof of the fix: the delegated task completed WHILE the blocking
    # confirm() call was still sleeping on its worker thread, not after.
    assert marker["delegated_work_completed_at"] < marker["confirm_finished_at"], (
        "Expected the in-flight delegated task to complete DURING the "
        "thread-offloaded blocking call (proving the event loop stayed "
        "free), but it completed only after -- the fix is not working as "
        "expected."
    )
