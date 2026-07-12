"""Thread-offloaded replacement for ``prompt_toolkit.patch_stdout.patch_stdout``.

CONFIRMED BUG (see docs/ISSUE_CASE_STUDIES.md-style investigation notes in the
PR/issue this module fixes): ``prompt_toolkit.patch_stdout.StdoutProxy``'s
internal ``_write_and_flush()`` method hardcodes::

    run_in_terminal(write_and_flush, in_executor=False)

(``prompt_toolkit/patch_stdout.py``, inside ``StdoutProxy._write_and_flush``).

``in_executor=False`` means the actual OS-level terminal write (a real
blocking ``write()`` + ``flush()`` syscall against ``sys.stdout``) is executed
SYNCHRONOUSLY on the asyncio event-loop thread, via
``loop.call_soon_threadsafe(write_and_flush_in_loop)``. If that syscall
blocks -- which happens whenever the terminal consumer (a busy or
backgrounded tmux pane, a slow SSH link, etc.) isn't draining its pty fast
enough to keep up with a large buffered write -- the ENTIRE event loop
freezes. Not just the write: every other task, timer, and callback
scheduled on that loop (including unrelated ones, like a bash tool's own
``asyncio.wait_for(...)`` timeout completion) cannot run AT ALL until the
blocking write returns. This was proven via a real pty pair with a
deliberately un-drained read side: the process froze solid and had to be
killed externally.

``prompt_toolkit`` does not expose any public way to opt a ``patch_stdout()``
session into ``in_executor=True`` -- it is not a constructor parameter of
``StdoutProxy`` or ``patch_stdout()``, only an internal implementation
detail. The fix here is a scoped monkeypatch: for the duration of our
``patch_stdout_offloaded()`` context, we replace the ``run_in_terminal``
name that ``prompt_toolkit.patch_stdout`` module resolves at call time
(imported there via ``from .application import ... run_in_terminal``) with
a thin wrapper that forces ``in_executor=True``. This routes the actual
write through prompt_toolkit's own ``run_in_executor_with_context()``
thread-pool offload (the same mechanism prompt_toolkit itself uses for
"long blocking functions" per its own docstring), so a stuck write blocks
only that worker thread -- never the event loop. Everything else about
``StdoutProxy`` (buffering, raw/ANSI handling, sleep-between-writes
coalescing) is untouched; we don't duplicate or reimplement any of its
write logic, so we don't drift from upstream changes to it.

The monkeypatch is scoped to the context manager (installed on ``__enter__``,
restored on ``__exit__``) rather than applied permanently at import time, so
it cannot leak into unrelated uses of prompt_toolkit elsewhere in the
process and is easy to reason about / test in isolation.

Reentrancy: install/restore is guarded by a module-level depth counter (see
``_install_depth`` below) so nested or overlapping ``patch_stdout_offloaded()``
contexts -- even ones that exit out of order relative to their entry, e.g.
two concurrent call sites both active at once -- always converge back to the
TRUE original ``run_in_terminal`` once the last active context exits, rather
than potentially leaving the wrapper permanently installed process-wide.
"""

from __future__ import annotations

import inspect
import threading
from contextlib import contextmanager
from typing import Any
from typing import Callable
from typing import Generator
from typing import TypeVar

import prompt_toolkit.patch_stdout as _pt_patch_stdout_module
from prompt_toolkit.application import run_in_terminal as _pt_run_in_terminal
from prompt_toolkit.patch_stdout import patch_stdout as _pt_patch_stdout

_T = TypeVar("_T")

__all__ = ["patch_stdout_offloaded"]

# Reentrancy guard state (module-level, protected by `_install_lock`). Only
# the outermost `__enter__` (depth 0 -> 1) captures the true original and
# installs the wrapper; only the outermost `__exit__` (depth 1 -> 0) restores
# it. This makes nested AND out-of-order-overlapping uses converge correctly.
_install_lock = threading.Lock()
_install_depth = 0
_true_original_run_in_terminal: Callable[..., Any] | None = None


def _run_in_terminal_forcing_executor(
    func: Callable[[], _T],
    render_cli_done: bool = False,
    in_executor: bool = False,
) -> Any:
    """Wrapper around ``prompt_toolkit``'s ``run_in_terminal`` that forces
    ``in_executor=True`` regardless of what the caller (StdoutProxy) passes.

    ``in_executor`` is accepted (and ignored) purely to keep the exact call
    signature ``StdoutProxy._write_and_flush``'s inner
    ``write_and_flush_in_loop()`` uses -- ``run_in_terminal(write_and_flush,
    in_executor=False)`` -- so this is a drop-in replacement at the name
    ``prompt_toolkit.patch_stdout.run_in_terminal``.
    """
    del in_executor  # always overridden below -- that is the entire point of this fix
    return _pt_run_in_terminal(func, render_cli_done=render_cli_done, in_executor=True)


def _assert_run_in_terminal_is_patchable() -> None:
    """Fail loud if ``prompt_toolkit.patch_stdout`` no longer exposes a
    ``run_in_terminal`` name matching the shape this fix depends on.

    This monkeypatch depends on an internal implementation detail: a plain
    module-level name (not part of ``prompt_toolkit.patch_stdout.__all__``)
    that a future ``prompt_toolkit`` release could rename, remove, or
    restructure (e.g. inlining the import) with zero warning. If that
    happens silently, this fix becomes a silent no-op -- reverting to the
    exact event-loop-freeze bug it exists to close, with no test signal
    until it's hit live again. So: verify the name exists and is callable
    with the expected ``in_executor`` parameter our wrapper overrides, and
    raise a clear, actionable error (naming the installed prompt_toolkit
    version) if not, rather than silently degrading.
    """
    import prompt_toolkit

    installed_version = prompt_toolkit.__version__

    current = getattr(  # pyright: ignore[reportPrivateImportUsage]
        _pt_patch_stdout_module, "run_in_terminal", None
    )
    if current is None:
        raise RuntimeError(
            "amplifier_app_cli.stdout_offload: prompt_toolkit.patch_stdout no "
            f"longer exposes a 'run_in_terminal' name (prompt_toolkit=="
            f"{installed_version}). This breaks the stdout-offload freeze fix: "
            "the scoped monkeypatch in patch_stdout_offloaded() has nothing to "
            "install, and StdoutProxy would silently fall back to its original "
            "in_executor=False blocking write path (the exact event-loop-freeze "
            "bug this fix closes). Update amplifier_app_cli/stdout_offload.py's "
            "monkeypatch target to match the new prompt_toolkit internals, or "
            "pin prompt_toolkit to a known-compatible version."
        )

    try:
        signature = inspect.signature(current)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "amplifier_app_cli.stdout_offload: prompt_toolkit.patch_stdout."
            f"run_in_terminal (prompt_toolkit=={installed_version}) is no longer "
            f"introspectable ({exc}). Cannot verify it matches the expected "
            "call shape; update amplifier_app_cli/stdout_offload.py."
        ) from exc

    if "in_executor" not in signature.parameters:
        raise RuntimeError(
            "amplifier_app_cli.stdout_offload: prompt_toolkit.patch_stdout."
            f"run_in_terminal (prompt_toolkit=={installed_version}) no longer "
            f"accepts an 'in_executor' parameter (signature: {signature}). "
            "The stdout-offload freeze fix depends on overriding that "
            "parameter; without it, the monkeypatch would silently stop "
            "forcing the offload. Update amplifier_app_cli/stdout_offload.py's "
            "wrapper to match the new signature, or pin prompt_toolkit to a "
            "known-compatible version."
        )


@contextmanager
def patch_stdout_offloaded(raw: bool = False) -> Generator[None, None, None]:
    """Drop-in replacement for ``prompt_toolkit.patch_stdout.patch_stdout(raw=...)``.

    Identical observable behavior (writes appear above the pinned prompt,
    ANSI passthrough when ``raw=True``), except the underlying
    ``StdoutProxy``'s terminal writes are offloaded to a thread so a
    blocked/backpressured write cannot freeze the asyncio event loop for the
    whole session -- including any in-process delegated sub-agents sharing
    this process's stdout (``session_spawner.py``).

    Reentrant: nested or overlapping calls (even ones whose exits don't
    mirror their entry order) are safe -- only the outermost active context
    installs/restores the monkeypatch (see module-level ``_install_depth``).
    """
    global _install_depth, _true_original_run_in_terminal

    # `run_in_terminal` is a plain module-level name inside prompt_toolkit's
    # patch_stdout module (imported there via `from .application import ...
    # run_in_terminal`), not part of its public `__all__` -- hence the
    # getattr/setattr (with pyright suppressed) instead of attribute access,
    # to be explicit that this is an intentional, scoped monkeypatch of an
    # internal name rather than a typo against the public API.
    with _install_lock:
        if _install_depth == 0:
            _assert_run_in_terminal_is_patchable()
            _true_original_run_in_terminal = getattr(  # pyright: ignore[reportPrivateImportUsage]
                _pt_patch_stdout_module, "run_in_terminal"
            )
            setattr(  # noqa: B010 - intentional scoped monkeypatch, see module docstring
                _pt_patch_stdout_module,
                "run_in_terminal",
                _run_in_terminal_forcing_executor,
            )
        _install_depth += 1

    try:
        with _pt_patch_stdout(raw=raw):
            yield
    finally:
        with _install_lock:
            _install_depth -= 1
            if _install_depth == 0:
                setattr(  # noqa: B010
                    _pt_patch_stdout_module,
                    "run_in_terminal",
                    _true_original_run_in_terminal,
                )
                _true_original_run_in_terminal = None
