"""Regression tests for ``amplifier_app_cli.dedicated_tty_input``.

Proves the mechanism that stops a competing TTY reader from freezing the
event loop: prompt_toolkit's default input path shares fd 0 with the
parent shell and any inherited children. If another process (ssh, a
digital-twin `exec`, a nested `amplifier` invocation) drains a byte off
that shared fd between prompt_toolkit's readiness check (``select.select``)
and its actual read (``os.read``), the read blocks forever on the
event-loop thread -- see the module docstring in
``amplifier_app_cli/dedicated_tty_input.py`` for the full mechanism.

These tests attach a REAL pty to fd 0 (mirroring an actual interactive
session) and verify:

1. The dedicated input's fd is distinct from fd 0 (its own open file
   description, not a share of fd 0's).
2. The dedicated fd privately carries ``O_NONBLOCK``.
3. fd 0's own open file description does NOT carry ``O_NONBLOCK`` --
   the regression guard against the "leaked O_NONBLOCK onto the shared
   fd" trap the fix must avoid (see module docstring).
4. A real, non-blocking ``os.read()`` on the dedicated fd with nothing
   pending does not block -- it raises ``BlockingIOError`` immediately
   instead of hanging (the OS-level guarantee the whole fix rests on).
5. prompt_toolkit's own ``PosixStdinReader.read()`` -- the exact call
   path in the confirmed freeze stack trace -- degrades that same
   ``BlockingIOError`` to an empty string instead of propagating it,
   reproducing the select-says-ready-but-read-finds-nothing race
   deterministically (no timing dependence, no forking, no real second
   reader process).

Fallback behavior (no controlling terminal, stdin not a tty, Windows,
``/dev/tty`` unavailable) is covered separately and does not require a
real pty.
"""

from __future__ import annotations

import fcntl
import os
import pty
import sys

import pytest
from amplifier_app_cli import dedicated_tty_input as dti


def _is_nonblocking(fd: int) -> bool:
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    return bool(flags & os.O_NONBLOCK)


@pytest.fixture
def pty_on_stdin(monkeypatch):
    """Attach a real pty's slave side to fd 0, saving/restoring the original.

    Also points ``dedicated_tty_input``'s tty-device seam at the pty
    slave's own path so production code (which normally opens
    ``/dev/tty``) opens a REAL, controllable device in the test instead
    of requiring a genuine controlling-terminal setup (``setsid`` +
    ``TIOCSCTTY``), which pytest's own process doesn't have.
    """
    master_fd, slave_fd = pty.openpty()
    slave_path = os.ttyname(slave_fd)

    saved_stdin_fd = os.dup(0)
    os.dup2(slave_fd, 0)
    monkeypatch.setattr(dti, "_TTY_DEVICE_PATH", slave_path)

    try:
        yield master_fd, slave_fd, slave_path
    finally:
        os.dup2(saved_stdin_fd, 0)
        os.close(saved_stdin_fd)
        os.close(slave_fd)
        os.close(master_fd)


def test_dedicated_fd_is_distinct_and_nonblocking_without_leaking_to_fd0(
    pty_on_stdin,
):
    """Core regression guard: dedicated fd is separate + non-blocking;
    fd 0 itself is left completely untouched.
    """
    handle = dti.open_dedicated_tty_input()
    assert handle is not None, (
        "Expected a dedicated input to be created against the test pty"
    )

    try:
        dedicated_fd = handle.input.fileno()

        assert dedicated_fd != 0, "Dedicated input must use its own fd, not share fd 0"

        assert _is_nonblocking(dedicated_fd) is True, (
            "Dedicated fd must carry O_NONBLOCK on its own open file description"
        )

        assert _is_nonblocking(0) is False, (
            "fd 0's open file description must be untouched -- setting "
            "O_NONBLOCK there would leak out to the parent shell and any "
            "inherited children (the OFD-leak trap)"
        )
    finally:
        handle.close()


def test_dedicated_fd_read_does_not_block_with_nothing_pending(pty_on_stdin):
    """Direct OS-level guarantee the whole fix rests on: a real ``os.read()``
    on the dedicated fd, with nothing written to the pty, must not block --
    it must raise ``BlockingIOError`` immediately.
    """
    handle = dti.open_dedicated_tty_input()
    assert handle is not None

    try:
        dedicated_fd = handle.input.fileno()
        with pytest.raises(BlockingIOError):
            os.read(dedicated_fd, 1024)
    finally:
        handle.close()


def test_posix_stdin_reader_degrades_race_to_empty_read(pty_on_stdin):
    """Reproduces the CONFIRMED freeze mechanism's exact call path
    deterministically: ``select.select`` reports the fd ready, but by the
    time ``os.read`` executes there is nothing left (a competing reader
    drained it in between). On the dedicated non-blocking fd,
    prompt_toolkit's own ``PosixStdinReader.read()`` -- the function at
    the top of the confirmed freeze stack trace -- must degrade this to
    an empty string rather than raising or blocking.
    """
    from prompt_toolkit.input import posix_utils as pt_posix_utils

    handle = dti.open_dedicated_tty_input()
    assert handle is not None

    try:
        dedicated_fd = handle.input.fileno()
        reader = pt_posix_utils.PosixStdinReader(dedicated_fd, encoding="utf-8")

        class _AlwaysReadyFakeSelect:
            """Stand-in for the ``select`` module: reports the fd ready
            unconditionally, forcing the exact race prompt_toolkit's real
            ``select.select(..., timeout=0)`` guard is meant to prevent
            but occasionally loses to under real scheduling timing.
            """

            @staticmethod
            def select(rlist, wlist, xlist, timeout):
                return (rlist, [], [])

        real_select = getattr(  # noqa: B009 - pyright: ignore[reportPrivateImportUsage]
            pt_posix_utils, "select"
        )
        setattr(pt_posix_utils, "select", _AlwaysReadyFakeSelect)  # noqa: B010
        try:
            result = reader.read()
        finally:
            setattr(pt_posix_utils, "select", real_select)  # noqa: B010

        assert result == "", (
            "PosixStdinReader.read() must degrade a would-block race to an "
            f"empty string, not raise or hang. Got: {result!r}"
        )
    finally:
        handle.close()


# ---------------------------------------------------------------------------
# Fallback behavior -- no real pty required.
# ---------------------------------------------------------------------------


def test_open_dedicated_tty_input_returns_none_when_stdin_not_a_tty(monkeypatch):
    """Piped/redirected stdin (CI, non-interactive use): must gracefully
    fall back to ``None`` rather than raising.

    Checked against fd 0 directly (``os.isatty(0)``, what the production
    code actually calls) rather than ``sys.stdin.isatty()`` -- the latter
    is unreliable under pytest, which replaces ``sys.stdin`` with a
    capture stand-in whose ``isatty()`` doesn't reflect fd 0's real state.
    """
    monkeypatch.setattr(dti.os, "isatty", lambda fd: False)
    assert dti.open_dedicated_tty_input() is None


def test_open_dedicated_tty_input_returns_none_when_tty_device_unopenable(
    monkeypatch,
):
    """No controlling terminal / ``/dev/tty`` unavailable (containers):
    must gracefully fall back to ``None`` rather than raising.
    """
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)

    def _raise_open(*args, **kwargs):
        raise OSError("simulated: no controlling terminal")

    monkeypatch.setattr(dti.os, "open", _raise_open)
    assert dti.open_dedicated_tty_input() is None


def test_open_dedicated_tty_input_returns_none_on_windows(monkeypatch):
    """Windows has no ``/dev/tty`` / POSIX ``Vt100Input`` concept: must
    gracefully fall back to ``None``.
    """
    monkeypatch.setattr(dti.sys, "platform", "win32")
    assert dti.open_dedicated_tty_input() is None


# ---------------------------------------------------------------------------
# Fail-loud guard -- prompt_toolkit internals this fix depends on.
# ---------------------------------------------------------------------------


def test_assert_guard_raises_if_posix_stdin_reader_stops_degrading_would_block():
    """If a future prompt_toolkit release stops swallowing non-blocking
    read errors in ``PosixStdinReader.read()``, the fix's core assumption
    is broken. The guard must fail loud (naming the installed version)
    instead of silently reintroducing the freeze.
    """
    import prompt_toolkit.input.posix_utils as pt_posix_utils

    class _BrokenPosixStdinReader:
        def __init__(self, fd, encoding):
            pass

        def read(self, count=1024):
            raise BlockingIOError("simulated: no longer swallowed")

    original = pt_posix_utils.PosixStdinReader
    pt_posix_utils.PosixStdinReader = _BrokenPosixStdinReader
    try:
        with pytest.raises(RuntimeError) as exc_info:
            dti._assert_posix_stdin_reader_degrades_nonblocking_reads()
    finally:
        pt_posix_utils.PosixStdinReader = original

    import prompt_toolkit

    assert prompt_toolkit.__version__ in str(exc_info.value)
