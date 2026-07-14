"""Transcript viewport and streamed-response adapter for the layered REPL."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from io import StringIO
from threading import RLock

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.mouse_events import MouseEvent
from prompt_toolkit.mouse_events import MouseButton
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.selection import SelectionType
from rich.console import Console

from ..console import Markdown
from .stream_status import StreamStatusTracker
from .terminal_transcript import TerminalTranscript


_SELECTION_TIMEOUT_SECONDS = 5.0
_TRANSCRIPT_WINDOW_LINES = 512


class _TranscriptLexer(Lexer):
    def __init__(self, view: LayeredTranscriptView) -> None:
        self._view = view

    def lex_document(self, document: Document) -> Callable[[int], StyleAndTextTuples]:
        def get_line(line_number: int) -> StyleAndTextTuples:
            return list(self._view.formatted_line(line_number))

        return get_line


class _TranscriptBufferControl(BufferControl):
    """Keep wheel navigation inside the transcript without stealing input focus."""

    def __init__(self, view: LayeredTranscriptView) -> None:
        self._view = view
        self._selection_anchor: int | None = None
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
            self._cursor_before_selection = None
            self._follow_before_selection = None
            self._view._request_redraw()
            return None
        return super().mouse_handler(mouse_event)

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


class LayeredTranscriptView:
    """Own immutable terminal output and expose a scrollable chat viewport."""

    def __init__(
        self,
        *,
        stream_status: StreamStatusTracker | None,
        render_width: Callable[[], int] | None = None,
        copy_selection: Callable[[str], bool] | None = None,
        max_lines: int | None = None,
    ) -> None:
        # ``max_lines`` now sizes only the presentation window. Transcript
        # storage remains unbounded for the lifetime of the session.
        requested_window = max_lines or _TRANSCRIPT_WINDOW_LINES
        self._window_capacity = max(
            128,
            min(_TRANSCRIPT_WINDOW_LINES, int(requested_window)),
        )
        self.buffer = Buffer(multiline=True, read_only=True)
        self.lexer = _TranscriptLexer(self)
        self.control = _TranscriptBufferControl(self)
        self._transcript = TerminalTranscript(max_lines=None)
        self._window_start = 0
        self._window_end = 0
        self._stream_status = stream_status
        self._render_width = render_width
        self._copy_selection = copy_selection
        self._invalidate: Callable[[], None] | None = None
        self._follow_tail = True
        self._lock = RLock()
        self._preview_cache: (
            tuple[
                str,
                int,
                tuple[str, ...],
                tuple[FormattedText, ...],
            ]
            | None
        ) = None

    def set_invalidate(self, invalidate: Callable[[], None]) -> None:
        self._invalidate = invalidate

    def append_output(self, text: str) -> None:
        """Capture output while keeping prompt-toolkit's loaded window bounded."""
        value = str(text)
        if not value:
            return
        with self._lock:
            self._transcript.write(value)
            # A paused viewport is immutable while new tail output arrives.
            # This preserves its global row, selection, and cursor exactly.
            if self._follow_tail:
                self._load_window_locked(
                    max(0, self._transcript.line_count - 1),
                    follow_tail=True,
                )
        self._request_redraw()

    def refresh_stream(self) -> None:
        """Invalidate replaceable stream content without mutating history."""
        self._request_redraw()

    def formatted_lines(self) -> tuple[FormattedText, ...]:
        with self._lock:
            return tuple(
                self._transcript.formatted_line(line_number)
                for line_number in range(self._window_start, self._window_end)
            )

    def formatted_line(self, line_number: int) -> FormattedText:
        """Return one loaded row, mapping viewport to global history."""
        with self._lock:
            global_row = self._window_start + int(line_number)
            if global_row < self._window_start or global_row >= self._window_end:
                return FormattedText()
            return self._transcript.formatted_line(global_row)

    def plain_text(self) -> str:
        with self._lock:
            return self._transcript.plain_text

    def copy_selected_text(self, text: str) -> bool:
        """Copy a user-selected transcript span without changing input focus."""
        if not text or self._copy_selection is None:
            return False
        try:
            return bool(self._copy_selection(text))
        except Exception:
            return False

    def scroll_page(self, direction: int, page_rows: int) -> None:
        """Move by global transcript rows, loading another window as needed."""
        with self._lock:
            line_count = self._transcript.line_count
            if line_count == 0:
                return
            current_row = self._window_start + self.buffer.document.cursor_position_row
        self.scroll_to_row(
            max(
                0,
                min(
                    line_count - 1,
                    current_row + direction * max(1, page_rows),
                ),
            )
        )

    def scroll_to_row(self, target_row: int) -> None:
        """Move to one global logical row, paging it into memory if needed."""
        with self._lock:
            line_count = self._transcript.line_count
            if line_count == 0:
                return
            target_row = max(0, min(line_count - 1, int(target_row)))
            self._follow_tail = target_row >= line_count - 1
            if self._follow_tail or not (
                self._window_start <= target_row < self._window_end
            ):
                self._load_window_locked(
                    target_row,
                    follow_tail=self._follow_tail,
                )
            else:
                local_row = target_row - self._window_start
                document = self.buffer.document
                cursor = document.translate_row_col_to_index(local_row, 0)
                self.buffer.set_document(
                    Document(document.text, cursor_position=cursor),
                    bypass_readonly=True,
                )
        self._request_redraw()

    def _load_window_locked(self, target_row: int, *, follow_tail: bool) -> None:
        """Load a bounded prompt-toolkit document around one global row."""
        line_count = self._transcript.line_count
        if line_count == 0:
            self._window_start = 0
            self._window_end = 0
            self.buffer.set_document(Document(""), bypass_readonly=True)
            return

        target_row = max(0, min(line_count - 1, int(target_row)))
        if follow_tail:
            start = max(0, line_count - self._window_capacity)
        else:
            start = max(0, target_row - (self._window_capacity // 2))
            start = min(start, max(0, line_count - self._window_capacity))
        end = min(line_count, start + self._window_capacity)
        rendered = "\n".join(self._transcript.plain_slice(start, end))
        local_row = target_row - start
        document = Document(rendered)
        cursor = (
            len(rendered)
            if follow_tail
            else document.translate_row_col_to_index(local_row, 0)
        )
        self._window_start = start
        self._window_end = end
        self.buffer.set_document(
            Document(rendered, cursor_position=cursor),
            bypass_readonly=True,
        )

    @property
    def following_tail(self) -> bool:
        return self._follow_tail

    @property
    def history_line_count(self) -> int:
        with self._lock:
            return self._transcript.line_count

    @property
    def loaded_line_count(self) -> int:
        with self._lock:
            return self._window_end - self._window_start

    @property
    def window_capacity(self) -> int:
        return self._window_capacity

    @property
    def window_start(self) -> int:
        with self._lock:
            return self._window_start

    @property
    def global_cursor_row(self) -> int:
        with self._lock:
            if self._transcript.line_count == 0:
                return 0
            return min(
                self._transcript.line_count - 1,
                self._window_start + self.buffer.document.cursor_position_row,
            )

    def preview_formatted_text(self) -> FormattedText:
        preview = self._stream_status.preview if self._stream_status else None
        if preview is None:
            return FormattedText()
        thinking = preview.kind in {"thinking", "reasoning"}
        label = "Thinking..." if thinking else "Responding..."
        style = "class:stream.thinking" if thinking else "class:stream.text"
        _, formatted = self._render_preview(preview.text)
        fragments: list[tuple[str, str]] = [("class:stream.label", label)]
        for line in formatted:
            fragments.append(("", "\n"))
            for fragment in line:
                ansi_style, text = fragment[0], fragment[1]
                fragments.append((f"{style} {ansi_style}".strip(), text))
        return FormattedText(fragments)

    def preview_plain_text(self) -> str:
        preview = self._stream_status.preview if self._stream_status else None
        if preview is None:
            return ""
        thinking = preview.kind in {"thinking", "reasoning"}
        preview_lines, _ = self._render_preview(preview.text)
        return "\n".join(
            ["Thinking..." if thinking else "Responding...", *preview_lines]
        )

    def preview_line_count(self) -> int:
        return max(1, self.preview_plain_text().count("\n") + 1)

    def _request_redraw(self) -> None:
        if self._invalidate is not None:
            self._invalidate()

    def _render_preview(
        self, text: str
    ) -> tuple[tuple[str, ...], tuple[FormattedText, ...]]:
        width = self._current_render_width()
        if (
            self._preview_cache is not None
            and self._preview_cache[0] == text
            and self._preview_cache[1] == width
        ):
            return self._preview_cache[2], self._preview_cache[3]
        sink = StringIO()
        preview_console = Console(
            file=sink,
            force_terminal=True,
            color_system="truecolor",
            no_color=False,
            width=width,
            height=25,
        )
        preview_console.print(Markdown(text))
        parsed = TerminalTranscript(max_lines=1_000)
        parsed.write(sink.getvalue())
        plain = parsed.plain_lines or ("",)
        formatted = parsed.formatted_lines or (FormattedText(),)
        self._preview_cache = (text, width, plain, formatted)
        return plain, formatted

    def _current_render_width(self) -> int:
        if self._render_width is None:
            return 80
        try:
            return max(20, min(240, int(self._render_width())))
        except (TypeError, ValueError, OSError):
            return 80


__all__ = ["LayeredTranscriptView"]
