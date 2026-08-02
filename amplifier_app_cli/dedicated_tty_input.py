"""Dedicated, non-blocking terminal input for prompt_toolkit -- stops a
competing TTY reader from freezing the CLI's asyncio event loop.

CONFIRMED BUG: real sessions froze completely for hours (one bash tool call
configured with a 35s timeout returned "Command timed out after 35 seconds"
4h04m later). 21 distinct freeze events across 19,028 real session logs, each
showing total process silence -- zero events anywhere in the project -- for
the entire gap. The freeze always ends the instant the user presses Enter.

Root cause, proven by a loop-thread stack captured mid-stall::

    prompt_toolkit/input/posix_utils.py:87 in read      <-- os.read(fd0, 1024) BLOCKED
    prompt_toolkit/input/vt100.py:98 in read_keys
    prompt_toolkit/application/application.py:686 in read_from_input
    asyncio/events.py:89 in _run
    asyncio/base_events.py:2050 in _run_once
    asyncio/base_events.py:683 in run_forever

``PosixStdinReader.read()`` (``prompt_toolkit/input/posix_utils.py``) does a
NON-ATOMIC select-then-read on a blocking fd::

    if not select.select([self.stdin_fd], [], [], 0)[0]:   # readiness check
        return ""
    ...
    data = os.read(self.stdin_fd, count)                   # BLOCKS

fd 0 has no ``O_NONBLOCK``; the tty is raw with ``VMIN=1``. If any other
process (``ssh`` without ``-n``, a digital-twin ``exec``, a nested
``amplifier`` invocation -- all observed in real sessions) consumes the
pending byte in the gap between the ``select()`` readiness check and the
``os.read()`` call, ``os.read`` parks forever. This is registered via
``loop.add_reader`` (``prompt_toolkit/input/vt100.py``), so it runs ON THE
EVENT LOOP THREAD -- ``run_forever`` cannot advance, and no asyncio timer,
task, or callback (including an unrelated bash tool's own
``asyncio.wait_for(...)`` timeout) can fire until the blocked read returns.

THE FIX: give prompt_toolkit its own dedicated file description for
terminal input, opened with ``O_NONBLOCK``, instead of sharing fd 0.
``PosixStdinReader.read()`` already wraps its ``os.read()`` call in
``except OSError: data = b""`` (the "In case of SIGWINCH" branch).
``BlockingIOError`` is a subclass of ``OSError``. So on a non-blocking fd,
the exact race this module exists to close degrades to an empty read
instead of a hang -- verified directly in
``tests/test_dedicated_tty_input.py`` (including a deterministic
reproduction of the select-says-ready-but-read-finds-nothing race, with no
timing dependence).

CRITICAL DETAIL -- why this does NOT just call
``os.set_blocking(0, False)``: ``O_NONBLOCK`` lives on the *open file
description* (OFD), which fd 0 shares with the parent shell and every
inherited child process. Setting it there would leak out of this process
and could break the user's shell (or any inherited child) after exit.
Instead, ``os.open("/dev/tty", os.O_RDONLY | os.O_NONBLOCK)`` creates a
FRESH OFD -- the flag is private to this new fd and cannot leak onto fd 0.
(As a bonus, Python's ``os.open()`` also makes the new fd non-inheritable
by default since PEP 446, so it isn't handed to bash-tool children either.)

WHY CONSTRUCTION OVER MONKEYPATCHING (contrast with
``stdout_offload.py``'s scoped monkeypatch of ``run_in_terminal``): here,
prompt_toolkit already exposes a fully PUBLIC, documented seam for this --
``Input`` objects are constructed from a plain ``TextIO``-like object and
handed to ``PromptSession(input=...)`` / ``Application(input=...)``. No
private name needs to be patched at all. Building a ``Vt100Input`` on our
own dedicated stream is simpler and more robust than intercepting an
internal call chain, so that is what this module does. The one true
internal dependency -- that ``PosixStdinReader.read()`` swallows
``BlockingIOError`` -- is guarded by
``_assert_posix_stdin_reader_degrades_nonblocking_reads()`` below, which
fails loud (naming the installed prompt_toolkit version) if that
assumption ever stops holding, rather than silently reintroducing the
freeze.

GRACEFUL FALLBACK: ``open_dedicated_tty_input()`` returns ``None`` --
never raises -- whenever the dedicated fd isn't available or applicable:
no controlling terminal, ``/dev/tty`` cannot be opened (CI, containers),
stdin is not a tty (piped input, non-interactive use), or Windows (no
``/dev/tty`` / POSIX ``Vt100Input`` concept there). Callers pass the
result straight through as ``PromptSession(input=...)``; ``None`` is
exactly prompt_toolkit's own default, so this is a transparent, safe
drop-in with zero UX change when the dedicated fd isn't available.
"""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prompt_toolkit.input.base import Input

# Seam for tests: production code always opens the process's controlling
# terminal. Tests point this at a real pty slave's own path instead, since
# constructing a genuine controlling-terminal setup (setsid + TIOCSCTTY)
# isn't available inside a pytest worker.
_TTY_DEVICE_PATH = "/dev/tty"

__all__ = [
    "DedicatedTtyInput",
    "close_dedicated_tty_input",
    "get_dedicated_tty_input",
    "open_dedicated_tty_input",
]


@dataclass
class DedicatedTtyInput:
    """A prompt_toolkit ``Input`` built on its own non-blocking fd, plus a
    ``close()`` to release that fd deterministically (no leak across
    sessions or spawned sub-sessions).
    """

    input: Input
    close: Callable[[], None]


def _assert_posix_stdin_reader_degrades_nonblocking_reads() -> None:
    """Fail loud if ``PosixStdinReader.read()`` no longer swallows a
    non-blocking read's ``BlockingIOError`` and instead lets it propagate.

    This is the one internal prompt_toolkit behavior this whole fix
    depends on (see module docstring). Verified here with a real
    ``os.pipe()`` and a monkeypatched ``select`` that unconditionally
    reports the fd ready -- deterministically reproducing the exact
    select-says-ready-but-nothing-to-read race a competing reader can
    cause, with no timing dependence and no real tty required.

    Raises ``RuntimeError`` naming the installed prompt_toolkit version if
    the assumption no longer holds, rather than silently constructing a
    dedicated input that can hang exactly like the bug this module fixes.
    """
    import prompt_toolkit
    from prompt_toolkit.input import posix_utils as pt_posix_utils

    installed_version = prompt_toolkit.__version__

    read_fd, write_fd = os.pipe()
    try:
        os.set_blocking(read_fd, False)
        reader = pt_posix_utils.PosixStdinReader(read_fd, encoding="utf-8")

        class _AlwaysReadyFakeSelect:
            """Stand-in for the ``select`` module: reports the fd ready
            unconditionally, forcing the race deterministically instead
            of relying on real (probabilistic) scheduling timing.
            """

            @staticmethod
            def select(rlist, wlist, xlist, timeout):
                return (rlist, [], [])

        # `select` is a plain module-level name inside prompt_toolkit's
        # posix_utils module (imported there via `import select`), not part
        # of its public `__all__` -- hence getattr/setattr (pyright
        # suppressed) instead of attribute access, matching
        # stdout_offload.py's own convention for touching internal names.
        real_select = getattr(  # noqa: B009 - pyright: ignore[reportPrivateImportUsage]
            pt_posix_utils, "select"
        )
        setattr(  # noqa: B010 - intentional scoped patch, restored in finally
            pt_posix_utils, "select", _AlwaysReadyFakeSelect
        )
        try:
            result = reader.read()
        except OSError as exc:
            raise RuntimeError(
                "amplifier_app_cli.dedicated_tty_input: prompt_toolkit "
                f"(=={installed_version})'s PosixStdinReader.read() no longer "
                f"swallows a non-blocking read's BlockingIOError ({exc!r}). "
                "This fix depends on that behavior to make a dedicated "
                "non-blocking input fd degrade a raced read to empty instead "
                "of propagating into the event loop. Update "
                "amplifier_app_cli/dedicated_tty_input.py or pin "
                "prompt_toolkit to a known-compatible version."
            ) from exc
        finally:
            setattr(pt_posix_utils, "select", real_select)  # noqa: B010

        if result != "":
            raise RuntimeError(
                "amplifier_app_cli.dedicated_tty_input: prompt_toolkit "
                f"(=={installed_version})'s PosixStdinReader.read() on an "
                f"empty non-blocking pipe returned {result!r} instead of "
                "'' -- the assumption this fix depends on no longer holds. "
                "Update amplifier_app_cli/dedicated_tty_input.py or pin "
                "prompt_toolkit to a known-compatible version."
            )
    finally:
        os.close(write_fd)
        os.close(read_fd)


def open_dedicated_tty_input() -> DedicatedTtyInput | None:
    """Build a prompt_toolkit ``Input`` on a fresh, non-blocking fd opened
    against the controlling terminal, instead of sharing fd 0.

    Returns ``None`` (never raises) whenever the dedicated fd isn't
    available or applicable -- Windows, stdin not a tty, no controlling
    terminal, or ``/dev/tty`` otherwise unopenable. Callers pass the
    result straight through as ``PromptSession(input=...)`` /
    ``Application(input=...)``: ``None`` is prompt_toolkit's own default,
    so falling back is a transparent, zero-UX-change no-op.
    """
    if sys.platform == "win32":
        # No /dev/tty, no POSIX Vt100Input on Windows -- fall back to
        # prompt_toolkit's own default (Win32Input on sys.stdin).
        return None

    try:
        # Checked against fd 0 directly (``os.isatty(0)``) rather than
        # ``sys.stdin.isatty()`` -- the fd is the thing that actually
        # matters here (it's what a competing reader races against), and
        # ``sys.stdin`` can be swapped for an unrelated object by test
        # runners, logging wrappers, etc. without changing fd 0 at all.
        if not os.isatty(0):
            # Piped/redirected stdin (CI, non-interactive use, tests):
            # nothing to dedicate a fd for -- fall back to the default.
            return None
    except OSError:
        # Defensive: fd 0 could be closed entirely in some embeddings.
        # Fall back rather than risk constructing something broken.
        return None

    try:
        fd = os.open(_TTY_DEVICE_PATH, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        # No controlling terminal (containers, detached processes) or
        # /dev/tty otherwise unopenable -- fall back to the default.
        return None

    try:
        _assert_posix_stdin_reader_degrades_nonblocking_reads()

        # Wrap the fd in a text file object -- Vt100Input needs
        # .fileno(), .isatty(), and .encoding. closefd=True (the default)
        # means closing this file object closes the underlying fd.
        stream = os.fdopen(fd, "r", encoding="utf-8", closefd=True)
    except BaseException:
        # Anything going wrong past this point (including the fail-loud
        # guard above) must not leak the fd we just opened.
        os.close(fd)
        raise

    try:
        from prompt_toolkit.input.vt100 import Vt100Input

        pt_input = Vt100Input(stream)
    except Exception:  # noqa: BLE001 - intentional: any construction failure must fall back, not crash the CLI
        # Construction failed for some reason not anticipated above
        # (e.g. a future prompt_toolkit release changing Vt100Input's
        # constructor contract) -- fall back rather than crash the CLI.
        stream.close()
        return None

    return DedicatedTtyInput(input=pt_input, close=stream.close)


# ---------------------------------------------------------------------------
# Process-wide singleton
#
# Every PromptSession the CLI creates (the main REPL prompt, and a fresh
# SteeringInputManager prompt each turn) should read the terminal through
# the SAME dedicated fd rather than each opening -- and leaking -- its own.
# Lazily created on first use, explicitly torn down via
# ``close_dedicated_tty_input()`` in the CLI's session-teardown path (no fd
# leak across sessions or spawned sub-sessions).
# ---------------------------------------------------------------------------

_singleton_lock = threading.Lock()
_singleton: DedicatedTtyInput | None = None
_singleton_attempted = False


def get_dedicated_tty_input() -> Input | None:
    """Return the process-wide dedicated terminal ``Input``, creating it on
    first call. Returns ``None`` (matching prompt_toolkit's own default)
    whenever a dedicated fd isn't available -- see
    ``open_dedicated_tty_input()`` for the fallback conditions.

    Safe to call repeatedly and from multiple call sites (the main REPL
    prompt, a fresh steering prompt each turn): construction is attempted
    at most once per process; every caller shares the same fd.
    """
    global _singleton, _singleton_attempted

    with _singleton_lock:
        if not _singleton_attempted:
            _singleton_attempted = True
            _singleton = open_dedicated_tty_input()
        return _singleton.input if _singleton is not None else None


def close_dedicated_tty_input() -> None:
    """Close the process-wide dedicated terminal fd, if one was created.

    Idempotent: safe to call even if ``get_dedicated_tty_input()`` was
    never called, or already closed. Call this once during session
    teardown -- never leave the dedicated fd open past the session that
    opened it.
    """
    global _singleton, _singleton_attempted

    with _singleton_lock:
        if _singleton is not None:
            _singleton.close()
        _singleton = None
        _singleton_attempted = False
