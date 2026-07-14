"""Typed event boundary for all immutable interactive transcript output."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import replace
from typing import TypeAlias

from rich.console import Console

from .transcript_blocks import TranscriptBlock
from .transcript_blocks import TranscriptRenderer
from .transcript_blocks import DebugBlock
from .transcript_blocks import UserBlock

UiEvent: TypeAlias = TranscriptBlock


class UiEventDispatcher:
    """Own the canonical renderer for one interactive transcript."""

    def __init__(
        self,
        console: Console,
        render_profile: str | Callable[[], str] | None = None,
        show_debug: bool | Callable[[], bool] = False,
    ) -> None:
        self._renderer = TranscriptRenderer(console, render_profile, show_debug)
        self._show_debug = show_debug
        self._latest_debug: DebugBlock | None = None

    def emit(self, event: UiEvent) -> None:
        if isinstance(event, UserBlock):
            self._latest_debug = None
        if isinstance(event, DebugBlock) and not event.expanded:
            if self._debug_is_visible():
                self._latest_debug = event
                self._renderer.render(event)
                return
            if self._latest_debug is not None:
                current = self._latest_debug
                self._latest_debug = DebugBlock(
                    (*current.lines, *event.lines),
                    label=(
                        current.label
                        if current.label == event.label
                        else "Internal output"
                    ),
                    total_lines=(current.total_lines or len(current.lines))
                    + (event.total_lines or len(event.lines)),
                )
                return
            self._latest_debug = event
        self._renderer.render(event)

    def emit_many(self, events: Iterable[UiEvent]) -> None:
        for event in events:
            self.emit(event)

    def bind_console(self, console: Console) -> None:
        """Route this dispatcher to the active transcript transport."""
        self._renderer.console = console

    def _debug_is_visible(self) -> bool:
        return bool(
            self._show_debug() if callable(self._show_debug) else self._show_debug
        )

    def expand_latest_debug(self) -> bool:
        if self._latest_debug is None:
            return False
        debug = self._latest_debug
        self._latest_debug = None
        self._renderer.render(replace(debug, expanded=True))
        return True

    def gap(self) -> None:
        """Emit structural whitespace without bypassing output ownership."""
        self._renderer.console.print()


__all__ = ["UiEvent", "UiEventDispatcher"]
