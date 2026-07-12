"""Regression tests closing three gaps identified by independent review of
the stdout-offload freeze fix (``amplifier_app_cli/stdout_offload.py``):

1. Reentrancy/nesting safety of the scoped ``run_in_terminal`` monkeypatch.
2. The write-time exception path (offloaded write raises mid-executor-call).
3. A loud, fail-fast guard against a future ``prompt_toolkit`` restructuring
   ``run_in_terminal`` out from under this monkeypatch (silent breakage).

These are unit tests (no pty, no fork, no ``@pytest.mark.integration``) --
they exercise ``patch_stdout_offloaded()``'s own monkeypatch machinery
directly, which is fast and does not require the freeze-reproduction
apparatus in ``test_stdout_offload_freeze_integration.py``.
"""

from __future__ import annotations

import inspect
from typing import Any
from typing import Callable

import prompt_toolkit.patch_stdout as _pt_patch_stdout_module
import pytest

from amplifier_app_cli.stdout_offload import _run_in_terminal_forcing_executor
from amplifier_app_cli.stdout_offload import patch_stdout_offloaded


def _current_run_in_terminal() -> Callable[..., Any]:
    """``run_in_terminal`` is a plain module-level name inside prompt_toolkit's
    ``patch_stdout`` module, not part of its public ``__all__`` -- hence the
    getattr (pyright suppressed) instead of attribute access, matching
    ``stdout_offload.py``'s own convention for touching this internal name.
    """
    return getattr(  # pyright: ignore[reportPrivateImportUsage]
        _pt_patch_stdout_module, "run_in_terminal"
    )


# ---------------------------------------------------------------------------
# Gap 1: reentrancy/nesting safety
# ---------------------------------------------------------------------------


def test_nested_patch_stdout_offloaded_restores_true_original_after_outer_exit():
    """Two properly-nested ``with patch_stdout_offloaded():`` blocks (the
    inner fully inside the outer, exiting LIFO) must leave the TRUE original
    ``run_in_terminal`` installed once the OUTER context exits -- not the
    inner's captured wrapper.
    """
    true_original = _current_run_in_terminal()

    with patch_stdout_offloaded():
        with patch_stdout_offloaded():
            pass
        # Inner has exited; outer is still active, so the monkeypatch must
        # still be installed (this is correct/expected, not a bug).
        assert _current_run_in_terminal() is not true_original

    assert _current_run_in_terminal() is true_original, (
        "After the OUTER patch_stdout_offloaded() context exited, the true "
        "original run_in_terminal must be restored."
    )


def test_overlapping_non_lifo_patch_stdout_offloaded_restores_true_original():
    """Simulates the genuinely hazardous case the review flagged: two
    'concurrent call sites both active at once' whose exits do NOT mirror
    their entries (not expressible via nested ``with`` blocks, since those
    always unwind LIFO). Context A enters, then context B enters while A is
    still active, then A exits *first* (out of order) while B is still
    active, then B exits last.

    Without a reentrancy guard: A captures the true original T and installs
    wrapper W. B captures W (already installed) as "its" original and
    re-installs W (no-op). A's early exit restores T -- prematurely, while B
    is still supposed to be active. B's later exit then restores what B
    captured (W), leaving W installed FOREVER after both contexts have
    exited. This test proves whether that actually happens.
    """
    true_original = _current_run_in_terminal()

    ctx_a = patch_stdout_offloaded()
    ctx_a.__enter__()
    ctx_b = patch_stdout_offloaded()
    ctx_b.__enter__()

    # Out-of-order exit: A first, even though B is still "active".
    ctx_a.__exit__(None, None, None)
    ctx_b.__exit__(None, None, None)

    assert _current_run_in_terminal() is true_original, (
        "After both overlapping (non-LIFO) contexts have exited, the true "
        "original run_in_terminal must be restored -- not left monkeypatched "
        "process-wide forever."
    )


# ---------------------------------------------------------------------------
# Gap 2: write-time exception path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_in_terminal_forcing_executor_propagates_write_exception():
    """If the offloaded write itself raises (simulating a broken pipe /
    disconnected pty mid-write), the exception must propagate back to
    whoever awaits the wrapper -- not be silently swallowed.
    """

    class _SimulatedBrokenPipe(OSError):
        pass

    def _raising_write() -> None:
        raise _SimulatedBrokenPipe("simulated broken pipe mid-write")

    with pytest.raises(_SimulatedBrokenPipe):
        await _run_in_terminal_forcing_executor(_raising_write)


@pytest.mark.asyncio
async def test_patch_stdout_offloaded_restores_original_when_body_raises():
    """The ``finally: setattr(...)`` restore in ``patch_stdout_offloaded``'s
    ``__exit__`` must still execute -- and correctly restore the true
    original ``run_in_terminal`` -- even when the code running inside the
    context (which is what observes/awaits a failing offloaded write) raises.
    """
    true_original = _current_run_in_terminal()

    class _SimulatedBrokenPipe(OSError):
        pass

    with pytest.raises(_SimulatedBrokenPipe):
        with patch_stdout_offloaded():
            assert _current_run_in_terminal() is not true_original
            raise _SimulatedBrokenPipe("simulated broken pipe mid-write")

    assert _current_run_in_terminal() is true_original, (
        "run_in_terminal must be restored to the true original even when the "
        "context body raised."
    )


# ---------------------------------------------------------------------------
# Gap 3: version-guard against silent breakage
# ---------------------------------------------------------------------------


def test_patch_stdout_offloaded_fails_loudly_if_run_in_terminal_attribute_absent(
    monkeypatch: pytest.MonkeyPatch,
):
    """Simulates a future ``prompt_toolkit`` release restructuring
    ``patch_stdout`` so it no longer exposes a module-level ``run_in_terminal``
    name. The fix must FAIL LOUD with a clear, actionable error naming the
    installed prompt_toolkit version -- not silently degrade back to the
    original unpatched (freeze-prone) behavior.
    """
    monkeypatch.delattr(_pt_patch_stdout_module, "run_in_terminal", raising=True)

    with pytest.raises(RuntimeError) as exc_info:
        with patch_stdout_offloaded():
            pass  # pragma: no cover - must not be reached

    message = str(exc_info.value)
    assert "run_in_terminal" in message
    import prompt_toolkit

    assert prompt_toolkit.__version__ in message, (
        "Error message must name the installed prompt_toolkit version so a "
        "future maintainer can immediately see what changed."
    )


def test_patch_stdout_offloaded_fails_loudly_if_run_in_terminal_shape_changes(
    monkeypatch: pytest.MonkeyPatch,
):
    """Simulates ``run_in_terminal`` still existing as a name, but no longer
    matching the expected callable shape (e.g. renamed/removed the
    ``in_executor`` parameter our wrapper relies on overriding). Must also
    fail loud rather than silently installing a wrapper that no longer does
    anything meaningful.
    """

    def _reshaped_run_in_terminal(func, render_cli_done: bool = False):
        # No `in_executor` parameter -- simulates an incompatible upstream
        # signature change.
        return func()

    monkeypatch.setattr(
        _pt_patch_stdout_module, "run_in_terminal", _reshaped_run_in_terminal
    )

    with pytest.raises(RuntimeError) as exc_info:
        with patch_stdout_offloaded():
            pass  # pragma: no cover - must not be reached

    assert "in_executor" in str(exc_info.value)


def test_run_in_terminal_forcing_executor_signature_matches_expected_shape():
    """Sanity check tying our own wrapper's signature to what the guard
    checks for, so the guard and the wrapper can't silently drift apart.
    """
    sig = inspect.signature(_run_in_terminal_forcing_executor)
    assert "in_executor" in sig.parameters
