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

import sys

import pytest

# These tests drive a real POSIX pseudo-terminal. `pty`, `termios` and `fcntl`
# are POSIX-only stdlib modules with no Windows equivalent, and they are
# imported at module scope -- so on Windows this file fails at COLLECTION,
# surfacing as a hard error rather than a skip. An `ImportError` during
# collection is indistinguishable in CI output from a genuine breakage, which
# is exactly the noise that trains people to ignore a red run.
#
# `allow_module_level=True` is required: a plain `pytestmark` is evaluated
# AFTER the module body executes, which is far too late to prevent the import
# itself from raising.
if sys.platform == "win32":
    pytest.skip(
        "POSIX-only: requires fcntl, pty, which have no Windows equivalent",
        allow_module_level=True,
    )


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
# Pollability probe (_fd_is_pollable) -- runs on ANY posix platform, no
# darwin gating: the probe is what decides candidate acceptance now, not a
# platform string.
# ---------------------------------------------------------------------------


def test_fd_is_pollable_true_for_real_pty_slave():
    """A real pty slave fd is exactly the kind of fd add_reader() must be
    able to register -- the probe must confirm it, not reject it."""
    master_fd, slave_fd = pty.openpty()
    try:
        assert dti._fd_is_pollable(slave_fd) is True
    finally:
        os.close(slave_fd)
        os.close(master_fd)


def test_fd_is_pollable_false_when_selector_registration_raises(monkeypatch):
    """If the platform's selector refuses registration (the real failure
    mode on macOS/BSD kqueue for the /dev/tty alias), the probe must
    report False -- and the selector it created must still be closed,
    even though registration failed.
    """
    closed = []

    class _FakeSelector:
        def register(self, fd, events):
            raise OSError(22, "Invalid argument")

        def unregister(self, fd):
            pass

        def close(self):
            closed.append(True)

    monkeypatch.setattr(dti.selectors, "DefaultSelector", lambda: _FakeSelector())

    assert dti._fd_is_pollable(0) is False
    assert closed == [True], "the selector must be closed even when register() raises"


# ---------------------------------------------------------------------------
# Candidate resolution + probe gate -- replaces the old platform-string
# check entirely. /dev/tty is tried first (the controlling-terminal alias,
# validated in production on Linux); os.ttyname(0) is the fallback every
# kqueue platform (macOS, the wider *BSD family) lands on. No platform
# string is consulted anywhere in the decision -- only _fd_is_pollable().
# See "OPENABLE IS NOT THE SAME AS POLLABLE" in the module docstring.
# ---------------------------------------------------------------------------


def test_candidate_fallthrough_to_ttyname_when_devtty_unpollable(monkeypatch):
    """Simulates the macOS scenario deterministically on ANY platform:
    the ``/dev/tty`` candidate opens cleanly but fails the pollability
    probe -- the function must reject it (closing the fd, no leak) and
    fall through to ``os.ttyname(0)``.

    A real controlling terminal isn't available inside a pytest worker,
    so ``os.open`` is patched to redirect a ``"/dev/tty"`` request to a
    second real pty instead -- giving the test a real, closeable fd to
    verify the reject-and-fall-through path against, without depending
    on the host's terminal state.
    """
    master_a, slave_a = pty.openpty()  # stands in for fd 0's own terminal
    master_b, slave_b = pty.openpty()  # stands in for whatever "/dev/tty" opens to
    slave_a_path = os.ttyname(slave_a)
    slave_b_path = os.ttyname(slave_b)

    saved_stdin_fd = os.dup(0)
    os.dup2(slave_a, 0)

    real_open = os.open
    real_close = os.close
    devtty_stand_in_fds: list[int] = []
    closed_fds: list[int] = []

    def _fake_open(path, flags, *args, **kwargs):
        if path == "/dev/tty":
            fd = real_open(slave_b_path, flags, *args, **kwargs)
            devtty_stand_in_fds.append(fd)
            return fd
        return real_open(path, flags, *args, **kwargs)

    def _spy_close(fd, *args, **kwargs):
        closed_fds.append(fd)
        return real_close(fd, *args, **kwargs)

    real_is_pollable = dti._fd_is_pollable

    def _fake_is_pollable(fd):
        # Identify the /dev/tty stand-in by DEVICE IDENTITY (os.ttyname),
        # not by raw fd number -- the rejected candidate's fd is closed
        # before the next candidate opens, and the OS is free to reuse
        # that exact fd number for the very next open() call, which would
        # make a raw-number comparison misidentify the real ttyname(0)
        # candidate as the rejected one.
        if os.ttyname(fd) == slave_b_path:
            return False  # simulate macOS kqueue rejecting the /dev/tty alias
        return real_is_pollable(fd)

    monkeypatch.setattr(dti.os, "open", _fake_open)
    monkeypatch.setattr(dti.os, "close", _spy_close)
    monkeypatch.setattr(dti, "_fd_is_pollable", _fake_is_pollable)

    try:
        handle = dti.open_dedicated_tty_input()
        assert handle is not None, (
            "the ttyname(0) fallback candidate must still succeed"
        )
        try:
            assert os.ttyname(handle.input.fileno()) == slave_a_path, (
                "must fall through to os.ttyname(0), not the rejected /dev/tty candidate"
            )
            assert devtty_stand_in_fds, (
                "the /dev/tty candidate must have been opened at all"
            )
            # No fd leak: the rejected candidate must have been explicitly
            # closed by production code (checked via a close() spy rather
            # than fstat -- the OS is free to reuse a just-closed fd number
            # for the very next open(), which would make an fstat-based
            # check pass even if the fd had never been closed at all).
            assert devtty_stand_in_fds[0] in closed_fds, (
                "the rejected /dev/tty candidate's fd must be closed, not leaked"
            )
        finally:
            handle.close()
    finally:
        os.dup2(saved_stdin_fd, 0)
        os.close(saved_stdin_fd)
        os.close(slave_a)
        os.close(master_a)
        os.close(slave_b)
        os.close(master_b)


def test_all_candidates_unpollable_returns_none_and_logs_warning(monkeypatch, caplog):
    """Every candidate opening but failing the pollability probe must
    fall back to ``None`` -- AND must log a warning naming the
    candidates tried, so a degraded fallback announces itself instead
    of failing silently.
    """
    master_fd, slave_fd = pty.openpty()
    saved_stdin_fd = os.dup(0)
    os.dup2(slave_fd, 0)
    try:
        monkeypatch.setattr(dti, "_fd_is_pollable", lambda fd: False)

        with caplog.at_level("WARNING"):
            handle = dti.open_dedicated_tty_input()

        assert handle is None
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert warnings, "must log a warning when every candidate is exhausted"
        assert any("candidate" in r.message.lower() for r in warnings), (
            "warning must name the candidates tried, not fail silently"
        )
    finally:
        os.dup2(saved_stdin_fd, 0)
        os.close(saved_stdin_fd)
        os.close(slave_fd)
        os.close(master_fd)


def test_repointed_seam_stays_authoritative_and_skips_ttyname_fallback(monkeypatch):
    """When ``_TTY_DEVICE_PATH`` is repointed away from its production
    default (the documented test seam), the function must open exactly
    the seam path and must NOT append ``os.ttyname(0)`` as a second
    candidate -- otherwise every seam-based test in this file would
    silently test the wrong device.
    """
    master_a, slave_a = pty.openpty()
    master_b, slave_b = pty.openpty()
    seam_path = os.ttyname(slave_b)

    saved_stdin_fd = os.dup(0)
    os.dup2(slave_a, 0)  # fd 0 is pty A; the seam points at pty B
    try:
        monkeypatch.setattr(dti, "_TTY_DEVICE_PATH", seam_path)

        opened: list[str] = []
        real_open = os.open

        def _spy_open(path, flags, *args, **kwargs):
            opened.append(path)
            return real_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(dti.os, "open", _spy_open)

        handle = dti.open_dedicated_tty_input()
        assert handle is not None
        try:
            assert opened == [seam_path], (
                "the repointed seam must be the ONLY candidate opened -- "
                f"os.ttyname(0) must never be appended when repointed, got {opened!r}"
            )
            assert os.ttyname(handle.input.fileno()) == seam_path, (
                "the repointed seam must stay authoritative"
            )
        finally:
            handle.close()
    finally:
        os.dup2(saved_stdin_fd, 0)
        os.close(saved_stdin_fd)
        os.close(slave_a)
        os.close(master_a)
        os.close(slave_b)
        os.close(master_b)


# ---------------------------------------------------------------------------
# macOS: kqueue cannot poll the /dev/tty alias device -- the fd must be
# opened on the underlying slave device (os.ttyname(0)) instead, or
# loop.add_reader() raises OSError(EINVAL) at attach time and interactive
# input is completely broken (silent instant exit on prompt_toolkit
# >=3.0.53, which converts the OSError to EOFError; an error loop on
# <=3.0.52, which lets it propagate). See "OPENABLE IS NOT THE SAME AS
# POLLABLE" in the module docstring. This end-to-end test still gates on
# real darwin/kqueue since it exercises the real event loop; the
# platform-agnostic candidate+probe unit tests above cover the same
# mechanism deterministically on any posix platform.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "darwin", reason="exercises real macOS kqueue")
@pytest.mark.asyncio
async def test_darwin_dedicated_input_attaches_to_real_kqueue_loop():
    """End-to-end regression test on real darwin, real event loop, seam at
    its production default: the dedicated input must register with the
    actual KqueueSelector loop and deliver bytes.

    On the unfixed code this bites in both environments: with a
    controlling terminal (developer machine) ``/dev/tty`` opens but
    ``attach()`` raises ``OSError(EINVAL)`` from kevent registration;
    without one (CI) ``/dev/tty`` cannot be opened at all so the handle
    is ``None`` and the assertion below fails. The fixed code resolves
    fd 0's slave device, which kqueue polls fine in both.
    """
    import asyncio

    master_fd, slave_fd = pty.openpty()
    saved_stdin_fd = os.dup(0)
    os.dup2(slave_fd, 0)
    try:
        handle = dti.open_dedicated_tty_input()
        assert handle is not None, (
            "with a pty on fd 0, darwin must produce a dedicated input "
            "even without a controlling terminal"
        )
        try:
            loop = asyncio.get_running_loop()
            got_keys: asyncio.Future = loop.create_future()

            def _on_ready() -> None:
                keys = handle.input.read_keys()
                if keys and not got_keys.done():
                    got_keys.set_result(keys)

            # attach() is where the unfixed code explodes on macOS:
            # loop.add_reader() -> kqueue kevent registration -> EINVAL.
            # raw_mode() mirrors production (a fresh pty is canonical, so
            # a lone byte would otherwise sit unreadable until newline).
            with handle.input.raw_mode(), handle.input.attach(_on_ready):
                os.write(master_fd, b"x")
                keys = await asyncio.wait_for(got_keys, timeout=5)

            assert keys[0].data == "x"
        finally:
            handle.close()
    finally:
        os.dup2(saved_stdin_fd, 0)
        os.close(saved_stdin_fd)
        os.close(slave_fd)
        os.close(master_fd)


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
