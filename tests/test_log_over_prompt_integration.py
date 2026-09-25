"""Real-pty A/B regression test for log records written over a live prompt.

What this guards
~~~~~~~~~~~~~~~~
``_configure_console_logging()`` (``main.py``) installs the process's only
root logging handler, at process start. ``main()`` calls it long before any
``patch_stdout()`` context is entered -- ``main.py:3853`` wraps the whole turn
(with the ``SteeringInputManager``'s anchored prompt live for its entire
duration), ``main.py:4014`` wraps the REPL prompt between turns. In an
interactive session a prompt_toolkit ``Application`` is therefore rendering
essentially all of the time.

``patch_stdout()`` works by rebinding the *names* ``sys.stdout`` and
``sys.stderr`` to a ``StdoutProxy`` (prompt_toolkit 3.0.52,
``patch_stdout.py``: ``sys.stdout = proxy`` / ``sys.stderr = proxy``). It
cannot reach an object that something else already captured -- and
``logging.StreamHandler.__init__`` captures eagerly (``self.stream = stream``),
with ``emit()`` writing to that captured object. So a stock
``StreamHandler(sys.stderr)`` built at process start writes *past* the proxy,
straight into a terminal prompt_toolkit currently owns, injecting text at the
cursor and scribbling over the input box.

Rich never had this bug because ``Console.file`` resolves ``sys.stdout``
lazily on every write (``rich/console.py:762``) -- reasoning this repo already
records as load-bearing at ``steering_input.py:10-14``. It was simply never
extended to ``logging``. ``_LateBoundStderrHandler`` extends it.

Why a pty, and why assert on raw bytes
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
This is a *terminal rendering* defect: both variants log the same string, at
the same level, through the same filters. Nothing about the record differs.
The only thing that differs is where the bytes land relative to the prompt
render -- which is observable only on a real terminal, in the raw byte
stream. A mock, a ``StringIO``, or a captured-``stderr`` assertion would pass
identically on the broken and the fixed build, i.e. would prove nothing.

The discriminator is structural, and is the mechanism itself rather than a
cosmetic detail: when a write reaches the proxy, prompt_toolkit routes it
through ``run_in_terminal``, which **erases the rendered app first** (a
cursor-left + erase-down prologue), writes the text on a clean line, and then
redraws the prompt below. A write that bypasses the proxy has no prologue --
the text simply appears wherever the cursor happened to be sitting.

Measured on Linux (prompt_toolkit 3.0.52), bytes between the last prompt
render and the log text::

    stock   ...PROMPTBOX>\\x1b[10D\\x1b[11C\\x1b[?7h\\x1b[0m\\x1b[?12l\\x1b[?25h
            WARNTEXT...                          <- at the cursor, no prologue
    fixed   ...PROMPTBOX>\\x1b[10D\\x1b[11C\\x1b[?7h\\x1b[0m\\x1b[?12l\\x1b[?25h
            \\x1b[11D\\x1b[J\\x1b[0m\\x1b[?7h\\x1b[?2004l\\x1b[?7h   <- erase prologue
            WARNTEXT...                          <- clean line
            \\x1b[?2004h...PROMPTBOX>...          <- prompt redrawn below

The warning is emitted from a **background asyncio task**, matching the real
trigger: ``amplifier-module-hooks-session-naming`` fires its check as a
fire-and-forget ``asyncio.create_task`` and returns immediately, so its
warning lands after the REPL has already re-entered ``prompt_async()``.
"""

from __future__ import annotations

import os
import sys
import time

import pytest

pytestmark = pytest.mark.integration

if sys.platform == "win32":  # pragma: no cover - see ci.yml matrix comment
    pytest.skip(
        "pty.fork() is POSIX-only; there is no Windows equivalent.",
        allow_module_level=True,
    )

from pty_harness import fork_pty_child

# Distinctive so it cannot collide with prompt_toolkit's own chatter (e.g. its
# "terminal doesn't support cursor position requests (CPR)" notice).
MARKER = "WARNTEXT_model_role_fast_resolved_to_openai"
PROMPT = "PROMPTBOX> "

ERASE_DOWN = b"\x1b[J"

# Child timeline (seconds). Generous relative to a render, but every wait in
# the parent is bounded -- a test may fail, a test may never hang.
_WARN_AT = 1.2
_EXIT_AT = 3.0
_CHILD_TIMEOUT = 20.0


def _child_body(fixed: bool) -> None:
    """Run a live prompt and log a warning from a background task."""
    import asyncio
    import logging

    # ``pty.fork()`` points fds 0/1/2 at the pty slave, but under pytest the
    # inherited ``sys.stdout``/``sys.stderr`` *objects* are still the runner's
    # capture wrappers, which write elsewhere entirely. Rebind them onto the
    # pty fds so the child is in the same state a real CLI process is: stdio
    # objects backed by a real terminal. Without this the child would render
    # into pytest's capture buffer and the pty would stay empty -- a test that
    # passes while exercising nothing.
    # stdin matters too: pytest replaces it with a reader that immediately
    # reports EOF, which makes ``prompt_async()`` return before the background
    # warning ever fires -- i.e. no prompt would be live at the moment under test.
    sys.stdin = os.fdopen(0, "r", closefd=False)
    sys.stdout = os.fdopen(1, "w", buffering=1, closefd=False)
    sys.stderr = os.fdopen(2, "w", buffering=1, closefd=False)

    from prompt_toolkit import PromptSession

    from amplifier_app_cli.stdout_offload import patch_stdout_offloaded as patch_stdout

    # --- replicate _configure_console_logging(), which runs at process start,
    # --- BEFORE any patch_stdout() context exists. Ordering is the bug.
    root = logging.getLogger()
    for stale in list(root.handlers):
        root.removeHandler(stale)

    handler: logging.Handler
    if fixed:
        from amplifier_app_cli.main import _LateBoundStderrHandler

        handler = _LateBoundStderrHandler()
    else:
        # Exactly what main.py:229 did before this fix.
        handler = logging.StreamHandler(sys.stderr)

    handler.setLevel(logging.WARNING)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.WARNING)

    log = logging.getLogger("amplifier_module_hooks_session_naming")

    async def _main() -> None:
        # Entered LATER than the handler was built -- as in main.py.
        with patch_stdout(raw=True):
            session: PromptSession = PromptSession()

            async def _warn_from_background() -> None:
                await asyncio.sleep(_WARN_AT)
                log.warning(MARKER)

            async def _quit() -> None:
                await asyncio.sleep(_EXIT_AT)
                os._exit(0)

            asyncio.create_task(_warn_from_background())
            asyncio.create_task(_quit())

            await session.prompt_async(PROMPT)

    asyncio.run(_main())


def _capture(fixed: bool) -> bytes:
    """Fork a pty child, let it run to completion, return everything it wrote."""
    # The child inherits this process's stdio buffers; anything still sitting
    # unflushed here would be flushed by the child straight into the pty and
    # would pollute the capture.
    sys.stdout.flush()
    sys.stderr.flush()

    child = fork_pty_child(lambda: _child_body(fixed))
    try:
        child.wait(_CHILD_TIMEOUT)
        # Let the drain thread pick up whatever landed just before exit.
        time.sleep(0.4)
        return child.output
    finally:
        child.kill()
        child.close()


def _segment_before_marker(data: bytes) -> bytes:
    """Bytes emitted between the last prompt render and the log text.

    This is the window the prologue must appear in. Slicing from the last
    ``PROMPT`` occurrence *before* the marker keeps the assertion insensitive
    to unrelated chatter elsewhere in the stream.
    """
    marker = MARKER.encode()
    idx = data.find(marker)
    assert idx != -1, (
        f"log record never reached the terminal at all; the scenario did not "
        f"exercise what it claims to. captured={data!r}"
    )
    prompt_idx = data.rfind(PROMPT.strip().encode(), 0, idx)
    assert prompt_idx != -1, (
        f"no prompt was rendered before the log record -- the prompt_toolkit "
        f"Application was not live, so this run proves nothing. captured={data!r}"
    )
    return data[prompt_idx:idx]


def test_stock_streamhandler_injects_log_text_at_the_cursor() -> None:
    """The defect, pinned: a stock handler writes with no erase prologue.

    This asserts the *broken* behavior on purpose. If prompt_toolkit or CPython
    ever changes such that an eagerly-bound handler stops corrupting the
    prompt, this test fails and the fix below can be re-evaluated rather than
    cargo-culted.
    """
    data = _capture(fixed=False)
    segment = _segment_before_marker(data)

    assert ERASE_DOWN not in segment, (
        "expected the stock StreamHandler to bypass the StdoutProxy and inject "
        "text at the cursor with no run_in_terminal erase prologue, but an "
        f"erase-down was present. segment={segment!r}"
    )


def test_late_bound_handler_routes_log_text_through_run_in_terminal() -> None:
    """The fix: the record is erased-for, written clean, and the prompt redrawn."""
    data = _capture(fixed=True)
    segment = _segment_before_marker(data)

    assert ERASE_DOWN in segment, (
        "expected _LateBoundStderrHandler to resolve sys.stderr at emit time and "
        "reach the patched StdoutProxy, so prompt_toolkit's run_in_terminal "
        "erases the rendered prompt before writing -- no erase-down found "
        f"between the prompt render and the log text. segment={segment!r}"
    )

    marker = MARKER.encode()
    after = data[data.find(marker) + len(marker) :]
    assert PROMPT.strip().encode() in after, (
        "run_in_terminal must redraw the prompt after the log text; no prompt "
        f"render followed the record. after={after!r}"
    )


def test_late_bound_handler_is_transparent_without_patch_stdout() -> None:
    """No proxy installed -> byte-identical behavior to a stock handler.

    Non-interactive runs, piped output and ``--output json`` never enter a
    ``patch_stdout()`` context. The fix must be inert there.
    """
    import logging
    from io import StringIO

    from amplifier_app_cli.main import _LateBoundStderrHandler

    handler = _LateBoundStderrHandler()
    handler.setLevel(logging.WARNING)
    handler.setFormatter(logging.Formatter("%(message)s"))

    original = sys.stderr
    buffer = StringIO()
    sys.stderr = buffer
    try:
        record = logging.LogRecord(
            name="t",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg=MARKER,
            args=(),
            exc_info=None,
        )
        handler.emit(record)
    finally:
        sys.stderr = original

    assert buffer.getvalue() == MARKER + "\n"


def test_configure_console_logging_installs_the_late_bound_handler() -> None:
    """Pins the wiring at ``main.py:229``.

    The pty tests above construct the handler directly, so they would keep
    passing even if ``_configure_console_logging()`` went back to a stock
    ``StreamHandler``. This is the assertion that notices.
    """
    import logging

    from amplifier_app_cli.main import (
        _configure_console_logging,
        _LateBoundStderrHandler,
    )

    root = logging.getLogger()
    saved = list(root.handlers)
    for stale in saved:
        root.removeHandler(stale)
    try:
        _configure_console_logging()
        installed = root.handlers
        assert len(installed) == 1
        assert isinstance(installed[0], _LateBoundStderrHandler), (
            "the root console handler must resolve sys.stderr at emit time; a "
            f"stock StreamHandler captures it at init. got={installed[0]!r}"
        )
    finally:
        for stale in list(root.handlers):
            root.removeHandler(stale)
        for original in saved:
            root.addHandler(original)


def test_late_bound_handler_honors_an_explicit_setstream() -> None:
    """``setStream(other)`` still pins, so the escape hatch is intact."""
    import logging
    from io import StringIO

    from amplifier_app_cli.main import _LateBoundStderrHandler

    handler = _LateBoundStderrHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))

    pinned = StringIO()
    handler.setStream(pinned)

    decoy = StringIO()
    original = sys.stderr
    sys.stderr = decoy
    try:
        record = logging.LogRecord(
            name="t",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg=MARKER,
            args=(),
            exc_info=None,
        )
        handler.emit(record)
    finally:
        sys.stderr = original

    assert pinned.getvalue() == MARKER + "\n"
    assert decoy.getvalue() == ""
