"""Typed event boundary for all immutable interactive transcript output."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import replace
from io import StringIO
from typing import Literal
from typing import TypeAlias
from typing import cast

from rich.console import Console

from .transcript_blocks import AnswerBlock
from .transcript_blocks import TranscriptBlock
from .transcript_blocks import TranscriptRenderer
from .transcript_blocks import DebugBlock
from .transcript_blocks import ToolBlock
from .transcript_blocks import ToolStatus
from .transcript_blocks import TurnTerminatorBlock
from .transcript_blocks import UserBlock

UiEvent: TypeAlias = TranscriptBlock

# One clickable transcript span: ``(kind, ref)``. The owning surface resolves
# refs to identities (checkpoint id, answer id) at emit time via
# ``set_click_ref_resolver``.
TranscriptClickKind: TypeAlias = Literal["tool", "terminator", "answer"]
TranscriptClickAction: TypeAlias = tuple[TranscriptClickKind, object]

_CLICKABLE_ANSWER_LABELS = frozenset({None, "Amplifier"})


class UiEventDispatcher:
    """Own the canonical renderer for one interactive transcript."""

    def __init__(
        self,
        console: Console,
        render_profile: str | Callable[[], str] | None = None,
        show_debug: bool | Callable[[], bool] = False,
    ) -> None:
        self._renderer = TranscriptRenderer(console, render_profile, show_debug)
        self._render_profile = render_profile
        self._show_debug = show_debug
        self._latest_debug: DebugBlock | None = None
        self._click_ref_resolver: (
            Callable[[TranscriptClickAction], TranscriptClickAction | None] | None
        ) = None
        self._active_click_action: TranscriptClickAction | None = None
        self._active_block: UiEvent | None = None

    def set_click_ref_resolver(
        self,
        resolver: Callable[[TranscriptClickAction], TranscriptClickAction | None],
    ) -> None:
        """Let the owning surface stamp identity onto clickable block spans."""
        self._click_ref_resolver = resolver

    @property
    def active_click_action(self) -> TranscriptClickAction | None:
        """Expose the click identity of the block currently being rendered."""
        return self._active_click_action

    @property
    def active_block(self) -> UiEvent | None:
        """Expose the immutable block currently being rendered, for retention."""
        return self._active_block

    def emit(self, event: UiEvent) -> None:
        self._active_click_action = self._click_action(event)
        self._active_block = event
        try:
            self._emit(event)
        finally:
            self._active_click_action = None
            self._active_block = None

    def render_to_ansi(self, event: UiEvent, *, width: int) -> str:
        """Re-render one retained block at a target width, off-transcript.

        Resize reflow uses this to rebuild history from source blocks. The
        console mirrors the bound transcript console's terminal posture and
        color system so a re-render at the emit width is byte-identical to
        the original emission.
        """
        base = self._renderer.console
        sink = StringIO()
        color_system = cast(
            Literal["standard", "256", "truecolor", "windows"] | None,
            base.color_system,
        )
        console = Console(
            file=sink,
            force_terminal=base.is_terminal,
            color_system=color_system,
            no_color=base.no_color,
            # Only a sane floor is enforced; no upper ceiling, so reflow at
            # real terminal widths above 240 columns re-renders correctly
            # instead of silently pinning to a stale 240-column wrap.
            width=max(20, int(width)),
            # Rich treats TERM=dumb as a fixed 80x25 terminal unless both
            # dimensions are explicit.  Reflow is an off-screen render, so a
            # stable height keeps the requested width authoritative in CI and
            # other dumb-terminal environments.
            height=25,
            legacy_windows=False,
        )
        TranscriptRenderer(console, self._render_profile, self._show_debug).render(
            event
        )
        return sink.getvalue()

    def _click_action(self, event: UiEvent) -> TranscriptClickAction | None:
        if isinstance(event, ToolBlock):
            clickable = (
                not event.expanded
                and bool(event.output)
                and event.status in {ToolStatus.COMPLETED, ToolStatus.FAILED}
            )
            action = ("tool", event) if clickable else None
        elif isinstance(event, TurnTerminatorBlock):
            action = ("terminator", event)
        elif isinstance(event, AnswerBlock) and event.label in _CLICKABLE_ANSWER_LABELS:
            action = ("answer", event)
        else:
            action = None
        if action is None or self._click_ref_resolver is None:
            return action
        try:
            return self._click_ref_resolver(action)
        except Exception:
            return None

    def _emit(self, event: UiEvent) -> None:
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


__all__ = [
    "TranscriptClickAction",
    "TranscriptClickKind",
    "UiEvent",
    "UiEventDispatcher",
]
