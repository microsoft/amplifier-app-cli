"""Debounced terminal-width reflow scheduling for the layered transcript.

Mirrors the Codex TUI resize contract: width changes observed during redraws
schedule a trailing ~75ms debounced rebuild so drag-resizes reflow once at the
final width; a reflow requested while a turn is streaming is deferred until
the turn completes; and the width that actually rebuilt history is tracked
separately from the width most recently observed, so a terminal that settles
on its final size after a rebuild still gets one more repair.

This module owns only scheduling state. The transcript view owns the rebuild
itself (``LayeredTranscriptView.reflow_to_width``).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

REFLOW_DEBOUNCE_SECONDS = 0.075

# One scheduled callback: ``schedule(delay_seconds, fire) -> cancel``.
ReflowScheduler = Callable[[float, Callable[[], None]], Callable[[], None]]


def _asyncio_scheduler(delay: float, fire: Callable[[], None]) -> Callable[[], None]:
    loop = asyncio.get_running_loop()
    handle = loop.call_later(delay, fire)
    return handle.cancel


class TranscriptReflowController:
    """Debounce width changes and defer reflow while a turn is streaming."""

    def __init__(
        self,
        *,
        observe_width: Callable[[], int],
        reflow: Callable[[int], bool],
        stream_active: Callable[[], bool] | None = None,
        schedule: ReflowScheduler | None = None,
        debounce_seconds: float = REFLOW_DEBOUNCE_SECONDS,
    ) -> None:
        self._observe_width = observe_width
        self._reflow = reflow
        self._stream_active = stream_active
        self._schedule = schedule
        self._debounce_seconds = max(0.0, float(debounce_seconds))
        self._reflowed_width: int | None = None
        self._pending_width: int | None = None
        self._cancel_pending: Callable[[], None] | None = None
        self._deferred_for_stream = False
        self._closed = False

    @property
    def reflowed_width(self) -> int | None:
        """Return the width the transcript was last rebuilt (or emitted) at."""
        return self._reflowed_width

    @property
    def pending(self) -> bool:
        return self._pending_width is not None

    @property
    def deferred_for_stream(self) -> bool:
        return self._deferred_for_stream

    def observe(self, _sender: object = None) -> None:
        """Sample the render width after a redraw and schedule any repair.

        The first observed width initializes the baseline without scheduling a
        rebuild: no old-width output exists yet. Later resize events push the
        trailing debounce deadline out so a drag reflows once, at rest.
        """
        if self._closed:
            return
        width = self._current_width()
        if width is None:
            return
        if self._reflowed_width is None:
            self._reflowed_width = width
            return
        if width == self._reflowed_width and self._pending_width is None:
            return
        if width == self._pending_width:
            return
        self._pending_width = width
        self._arm(self._debounce_seconds)

    def close(self) -> None:
        """Cancel any scheduled reflow permanently."""
        self._closed = True
        self._cancel_timer()
        self._pending_width = None
        self._deferred_for_stream = False

    def _current_width(self) -> int | None:
        try:
            return int(self._observe_width())
        except Exception:
            logger.debug("Could not observe transcript render width", exc_info=True)
            return None

    def _arm(self, delay: float) -> None:
        self._cancel_timer()
        schedule = self._schedule
        if schedule is None:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                # No loop to defer into: repair synchronously, undebounced.
                self._fire()
                return
            schedule = _asyncio_scheduler
        try:
            self._cancel_pending = schedule(delay, self._fire)
        except Exception:
            logger.debug("Could not schedule transcript reflow", exc_info=True)
            self._cancel_pending = None

    def _cancel_timer(self) -> None:
        cancel = self._cancel_pending
        self._cancel_pending = None
        if cancel is not None:
            try:
                cancel()
            except Exception:
                logger.debug("Could not cancel transcript reflow", exc_info=True)

    def _fire(self) -> None:
        self._cancel_pending = None
        if self._closed:
            return
        width = self._current_width()
        if width is None or width == self._reflowed_width:
            self._pending_width = None
            self._deferred_for_stream = False
            return
        if self._stream_is_active():
            # Rewrapping mid-stream would repaint under live output; hold the
            # request and poll until the turn completes, then rebuild once.
            self._deferred_for_stream = True
            self._pending_width = width
            self._arm(self._debounce_seconds)
            return
        self._pending_width = None
        self._deferred_for_stream = False
        # Record the width even when the rebuild reports it had nothing to do,
        # so an unreflowable transcript cannot re-arm the timer forever.
        self._reflowed_width = width
        try:
            self._reflow(width)
        except Exception:
            logger.debug("Transcript reflow failed", exc_info=True)

    def _stream_is_active(self) -> bool:
        if self._stream_active is None:
            return False
        try:
            return bool(self._stream_active())
        except Exception:
            return False


__all__ = ["REFLOW_DEBOUNCE_SECONDS", "TranscriptReflowController"]
