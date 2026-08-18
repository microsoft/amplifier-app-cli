"""Tests for the main-thread guard around SIGINT handler installation.

`signal.signal()` is only callable from the main thread of the main
interpreter. Anywhere else CPython raises:

    ValueError: signal only works in main thread of the main interpreter

Two interruptible phases in `amplifier_app_cli.commands.run` install SIGINT
handlers: the startup update check (GAP-023) and bundle preparation
(GAP-027). Neither installed a handler before those fixes, so both were safe
to call from any thread. Adding an unguarded `signal.signal()` call narrowed
that contract -- it makes those phases raise for any caller that does not own
the main thread.

No caller that actually reaches these phases off the main thread has been
identified; two candidate embedders were checked and neither reaches them.
These tests therefore guard a contract rather than reproduce an observed
field failure: they pin "safe to call from any thread" so it cannot be
removed again silently.

They exercise the real `_scoped_sigint_handler` and the real
`_run_startup_update_check`, not mocks of them, from a worker thread.
"""

from __future__ import annotations

import signal
import threading
from typing import Any
from unittest.mock import patch

from amplifier_app_cli.commands.run import (
    _run_startup_update_check,
    _scoped_sigint_handler,
)


def _noop_handler(signum: int, frame: Any) -> None:  # pragma: no cover - never invoked
    """Stand-in SIGINT handler. Installed and removed, never fired."""


def _run_in_worker_thread(fn: Any) -> dict[str, Any]:
    """Run `fn` on a non-main thread and capture its outcome.

    Returns a dict with either `result` or `exc`, so the caller can assert on
    a raised exception rather than having it vanish into the thread.
    """
    captured: dict[str, Any] = {}

    def target() -> None:
        assert threading.current_thread() is not threading.main_thread()
        try:
            captured["result"] = fn()
        except BaseException as exc:  # noqa: BLE001 - deliberately capturing everything
            captured["exc"] = exc

    thread = threading.Thread(target=target, name="sigint-guard-test-worker")
    thread.start()
    thread.join(timeout=30)
    assert not thread.is_alive(), "worker thread did not finish"
    return captured


def test_scoped_sigint_handler_declines_off_main_thread() -> None:
    """Off the main thread the guard declines instead of raising ValueError.

    This is the regression. Without the guard, `signal.signal()` raises
    `ValueError: signal only works in main thread of the main interpreter`
    and the caller dies.
    """

    def use_guard() -> bool:
        with _scoped_sigint_handler(_noop_handler) as installed:
            return installed

    captured = _run_in_worker_thread(use_guard)

    assert "exc" not in captured, (
        f"guard raised off the main thread: {captured.get('exc')!r} "
        "-- the main-thread guard is missing or ineffective"
    )
    assert captured["result"] is False, (
        "guard reported a handler was installed off the main thread; "
        "signal.signal() cannot succeed there"
    )


def test_scoped_sigint_handler_installs_and_restores_on_main_thread() -> None:
    """On the main thread the handler is installed and then restored.

    Guards against a fix that works by simply never installing anything.
    """
    assert threading.current_thread() is threading.main_thread()

    before = signal.getsignal(signal.SIGINT)

    with _scoped_sigint_handler(_noop_handler) as installed:
        assert installed is True
        assert signal.getsignal(signal.SIGINT) is _noop_handler

    assert signal.getsignal(signal.SIGINT) is before


def test_startup_update_check_does_not_raise_off_main_thread() -> None:
    """The real update-check phase survives being run off the main thread.

    Integration-level counterpart to the unit test above: exercises the
    actual function an embedder reaches, with only its network-touching
    dependency stubbed out.
    """

    async def _fake_check_and_notify() -> None:
        return None

    def run_check() -> str:
        with patch(
            "amplifier_app_cli.utils.startup_checker.check_and_notify",
            _fake_check_and_notify,
        ):
            _run_startup_update_check()
        return "completed"

    captured = _run_in_worker_thread(run_check)

    assert "exc" not in captured, (
        f"_run_startup_update_check raised off the main thread: {captured.get('exc')!r}"
    )
    assert captured["result"] == "completed"
