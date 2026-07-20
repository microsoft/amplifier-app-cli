"""Transcript viewport and streamed-response adapter for the layered REPL."""

from __future__ import annotations

from collections.abc import Callable
from io import StringIO
from threading import RLock

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import FormattedText
from rich.console import Console

from ..console import Markdown
from .layered_transcript_control import TranscriptBufferControl
from .layered_transcript_control import TranscriptLexer
from .stream_status import StreamStatusTracker
from .terminal_transcript import TerminalTranscript
from .transcript_click_spans import ClickSpanRegistry
from .transcript_click_spans import TranscriptSpan


_TRANSCRIPT_WINDOW_LINES = 512


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
        self.lexer = TranscriptLexer(self)
        self.control = TranscriptBufferControl(self)
        self._transcript = TerminalTranscript(max_lines=None)
        self._window_start = 0
        self._window_end = 0
        self._stream_status = stream_status
        self._render_width = render_width
        self._copy_selection = copy_selection
        self._invalidate: Callable[[], None] | None = None
        self._follow_tail = True
        self._lock = RLock()
        self._click_spans = ClickSpanRegistry()
        self._on_click_action: Callable[[object], bool] | None = None
        self._render_block: Callable[[object, int], str] | None = None
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

    def set_click_action_handler(self, handler: Callable[[object], bool]) -> None:
        """Route clicks on registered block spans to the owning application."""
        self._on_click_action = handler

    def set_block_renderer(self, render: Callable[[object, int], str]) -> None:
        """Provide the source-backed ``(block, width) -> ANSI`` reflow renderer."""
        self._render_block = render

    def append_output(
        self,
        text: str,
        action: object | None = None,
        block: object | None = None,
    ) -> None:
        """Capture output while keeping prompt-toolkit's loaded window bounded."""
        value = str(text)
        if not value:
            return
        with self._lock:
            start_row = self._transcript.line_count
            self._transcript.write(value)
            end_row = self._transcript.line_count - 1
            self._click_spans.record(start_row, end_row, action, block=block, raw=value)
            # A paused viewport is immutable while new tail output arrives.
            # This preserves its global row, selection, and cursor exactly.
            if self._follow_tail:
                self._load_window_locked(
                    max(0, self._transcript.line_count - 1),
                    follow_tail=True,
                )
        self._request_redraw()

    def click_action_at_row(self, global_row: int) -> object | None:
        """Return the block action registered for one global transcript row."""
        with self._lock:
            return self._click_spans.action_at(int(global_row))

    def activate_click_at_row(self, global_row: int) -> bool:
        """Dispatch a click on one transcript row to its registered action."""
        handler = self._on_click_action
        if handler is None:
            return False
        action = self.click_action_at_row(global_row)
        if action is None:
            return False
        try:
            return bool(handler(action))
        except Exception:
            return False

    def reflow_to_width(self, width: int) -> bool:
        """Rebuild history from retained sources at a new terminal width.

        Retained blocks re-render through the canonical Rich pipeline at the
        new width; untagged spans (resume replays, stray stdout) replay their
        raw ANSI verbatim. Click spans are rebuilt against the new rows, and
        the viewport returns to the tail when it was tailing, or stays
        anchored to the span it was paused on.
        """
        render = self._render_block
        if render is None:
            return False
        # No documented rationale ties reflow to a 240-column ceiling (see
        # ADR-0006); only a sane floor is enforced so real terminal widths
        # above 240 reflow correctly instead of silently pinning to 240.
        width = max(20, int(width))
        with self._lock:
            spans = self._click_spans.spans
            dropped = self._click_spans.dropped_count
            old_line_count = self._transcript.line_count
            was_tailing = self._follow_tail
            anchor_span, anchor_row = self._anchor_locked(spans)
            fresh = TerminalTranscript(max_lines=None)
            registry = ClickSpanRegistry(capacity=self._click_spans.capacity)
            registry.note_dropped(dropped)
            if dropped:
                fresh.write(
                    f"\x1b[2m… {dropped} earlier transcript chunks "
                    "dropped from reflow …\x1b[0m\n"
                )
            # How far into the anchor span the paused row actually sat, so
            # the rebuild can land on the same row -- not just the first row
            # of the span. This matters most for untagged raw writes: they
            # merge into one long span (see `ClickSpanRegistry._continues`),
            # so without preserving this offset a viewport paused deep
            # inside a long run of plain output would snap back to that
            # run's very first row on every reflow.
            anchor_offset = (
                max(0, anchor_row - anchor_span.start_row)
                if anchor_span is not None
                else 0
            )
            target_row = 0
            for span in spans:
                start_row = fresh.line_count
                fresh.write(self._reflowed_span_text(span, width, render))
                end_row = fresh.line_count - 1
                registry.record(
                    start_row, end_row, span.action, block=span.block, raw=span.raw
                )
                if span is anchor_span:
                    # Raw spans replay verbatim, so the offset lands exactly
                    # back on the paused row. A re-rendered block can change
                    # row count at the new width, so clamp to stay inside
                    # the span's rebuilt rows rather than overrunning it.
                    span_rows = max(0, end_row - start_row)
                    target_row = start_row + min(anchor_offset, span_rows)
            self._transcript = fresh
            self._click_spans = registry
            self._preview_cache = None
            line_count = fresh.line_count
            if was_tailing or line_count == 0:
                self._follow_tail = True
                self._load_window_locked(max(0, line_count - 1), follow_tail=True)
            else:
                if anchor_span is None and old_line_count > 1:
                    target_row = round(
                        anchor_row * (line_count - 1) / (old_line_count - 1)
                    )
                self._follow_tail = False
                self._load_window_locked(
                    min(max(0, target_row), line_count - 1),
                    follow_tail=False,
                )
        self._request_redraw()
        return True

    @staticmethod
    def _reflowed_span_text(
        span: TranscriptSpan,
        width: int,
        render: Callable[[object, int], str],
    ) -> str:
        if span.block is None:
            return span.raw
        try:
            rendered = render(span.block, width)
        except Exception:
            rendered = ""
        # An empty re-render (changed render profile, renderer error) falls
        # back to the width-stale raw chunk rather than dropping content.
        return rendered if rendered else span.raw

    def _anchor_locked(
        self, spans: tuple[TranscriptSpan, ...]
    ) -> tuple[TranscriptSpan | None, int]:
        """Return the span (and global row) the paused viewport sits on."""
        if self._transcript.line_count == 0:
            return None, 0
        row = min(
            self._transcript.line_count - 1,
            self._window_start + self.buffer.document.cursor_position_row,
        )
        for span in reversed(spans):
            if span.start_row <= row <= span.end_row:
                return span, row
        return None, row

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

    @property
    def retained_span_count(self) -> int:
        """Return how many rendered spans reflow retains right now."""
        with self._lock:
            return len(self._click_spans.spans)

    @property
    def dropped_span_count(self) -> int:
        """Return how many retained spans the registry bound has dropped."""
        with self._lock:
            return self._click_spans.dropped_count

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

    def current_render_width(self) -> int:
        """Expose the render width (floored, unclamped above) for resize observation."""
        return self._current_render_width()

    def _current_render_width(self) -> int:
        if self._render_width is None:
            return 80
        try:
            # Only a sane floor is enforced; see the comment in
            # `reflow_to_width` for why there is no upper ceiling.
            return max(20, int(self._render_width()))
        except (TypeError, ValueError, OSError):
            return 80


__all__ = ["LayeredTranscriptView"]
