"""Mouse and lexer plumbing for the layered transcript viewport."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING

from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.mouse_events import MouseEvent
from prompt_toolkit.mouse_events import MouseButton
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.selection import SelectionType

if TYPE_CHECKING:
    from .layered_transcript import LayeredTranscriptView


_SELECTION_TIMEOUT_SECONDS = 5.0


class TranscriptLexer(Lexer):
    def __init__(self, view: LayeredTranscriptView) -> None:
        self._view = view

    def lex_document(self, document: Document) -> Callable[[int], StyleAndTextTuples]:
        def get_line(line_number: int) -> StyleAndTextTuples:
            return list(self._view.formatted_line(line_number))

        return get_line


class TranscriptBufferControl(BufferControl):
    """Keep wheel navigation inside the transcript without stealing input focus."""

    def __init__(self, view: LayeredTranscriptView) -> None:
        self._view = view
        self._selection_anchor: int | None = None
        self._selection_dragged = False
        self._cursor_before_selection: int | None = None
        self._follow_before_selection: bool | None = None
        self._selection_generation = 0
        self._selection_timeout: asyncio.TimerHandle | None = None
        super().__init__(
            buffer=view.buffer,
            focusable=False,
            lexer=view.lexer,
        )

    def mouse_handler(self, mouse_event: MouseEvent):
        if mouse_event.event_type == MouseEventType.SCROLL_UP:
            self.cancel_incomplete_selection()
            self._view.scroll_page(-1, 3)
            return None
        if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
            self.cancel_incomplete_selection()
            self._view.scroll_page(1, 3)
            return None
        index = self._mouse_position_to_index(mouse_event)
        if index is None:
            return super().mouse_handler(mouse_event)
        if (
            mouse_event.event_type == MouseEventType.MOUSE_DOWN
            and mouse_event.button == MouseButton.LEFT
        ):
            self.cancel_incomplete_selection()
            self._cursor_before_selection = self.buffer.cursor_position
            self._follow_before_selection = self._view.following_tail
            self._view._follow_tail = False
            self._selection_anchor = index
            self._selection_dragged = False
            self.buffer.exit_selection()
            self.buffer.cursor_position = index
            self.buffer.start_selection(SelectionType.CHARACTERS)
            self._arm_selection_timeout()
            self._view._request_redraw()
            return None
        if (
            mouse_event.event_type == MouseEventType.MOUSE_MOVE
            and self._selection_anchor is not None
        ):
            if index != self._selection_anchor:
                self._selection_dragged = True
            self.buffer.cursor_position = index
            self._arm_selection_timeout()
            self._view._request_redraw()
            return None
        if (
            mouse_event.event_type == MouseEventType.MOUSE_UP
            and self._selection_anchor is not None
        ):
            self._cancel_selection_timeout()
            self.buffer.cursor_position = index
            selected = self.buffer.document.cut_selection()[1].text
            clicked = not self._selection_dragged and index == self._selection_anchor
            if selected:
                self._view._follow_tail = False
                self._view.copy_selected_text(selected)
            else:
                self.buffer.exit_selection()
                if self._cursor_before_selection is not None:
                    self.buffer.cursor_position = self._cursor_before_selection
                if self._follow_before_selection is not None:
                    self._view._follow_tail = self._follow_before_selection
            self._selection_anchor = None
            self._selection_dragged = False
            self._cursor_before_selection = None
            self._follow_before_selection = None
            if clicked and not selected:
                self._activate_click(index)
            self._view._request_redraw()
            return None
        return super().mouse_handler(mouse_event)

    def _activate_click(self, index: int) -> None:
        """Dispatch a stationary press-and-release to the row's block action."""
        try:
            row, _ = self.buffer.document.translate_index_to_position(index)
        except (IndexError, ValueError):
            return
        self._view.activate_click_at_row(self._view.window_start + row)

    def _mouse_position_to_index(self, mouse_event: MouseEvent) -> int | None:
        get_processed_line = getattr(self, "_last_get_processed_line", None)
        if get_processed_line is None:
            return None
        try:
            processed_line = get_processed_line(mouse_event.position.y)
            column = processed_line.display_to_source(mouse_event.position.x)
            return self.buffer.document.translate_row_col_to_index(
                mouse_event.position.y,
                column,
            )
        except (IndexError, TypeError, ValueError):
            return None

    @property
    def selection_in_progress(self) -> bool:
        return self._selection_anchor is not None

    def cancel_incomplete_selection(self) -> None:
        """Recover when a terminal reports release outside the transcript."""
        if self._selection_anchor is None:
            return
        self._cancel_selection_timeout()
        self.buffer.exit_selection()
        if self._cursor_before_selection is not None:
            self.buffer.cursor_position = self._cursor_before_selection
        if self._follow_before_selection is not None:
            self._view._follow_tail = self._follow_before_selection
        self._selection_anchor = None
        self._selection_dragged = False
        self._cursor_before_selection = None
        self._follow_before_selection = None
        self._view._request_redraw()

    def _arm_selection_timeout(self) -> None:
        self._cancel_selection_timeout()
        self._selection_generation += 1
        generation = self._selection_generation
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._selection_timeout = loop.call_later(
            _SELECTION_TIMEOUT_SECONDS,
            self._expire_selection,
            generation,
        )

    def _cancel_selection_timeout(self) -> None:
        if self._selection_timeout is not None:
            self._selection_timeout.cancel()
            self._selection_timeout = None

    def _expire_selection(self, generation: int) -> None:
        self._selection_timeout = None
        if generation == self._selection_generation:
            self.cancel_incomplete_selection()


__all__ = ["TranscriptBufferControl", "TranscriptLexer"]
