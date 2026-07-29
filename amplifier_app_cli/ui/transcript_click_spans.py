"""Bounded registry retaining rendered transcript spans for clicks and reflow.

One ordered registry unifies three concerns for the append-only transcript:

* click spans — which global rows dispatch which block action;
* block retention — the frozen ``TranscriptBlock`` that produced each span, so
  a terminal resize can re-render history at the new width from source;
* raw retention — the exact ANSI chunk that was written, replayed verbatim for
  untagged output (resume replays, stray stdout) and used as the fallback when
  a retained block cannot be re-rendered.

Spans arrive in row order because the transcript is append-only. A chunk that
rewrites the tail invalidates every span it overlaps, mirroring how the
viewport reloads its presentation window for the new rows. The registry is
bounded: the oldest spans are dropped first and the drop tally is preserved so
a reflow can surface one dropped-count line. The caller is responsible for
synchronization.
"""

from __future__ import annotations

from dataclasses import dataclass

_RETENTION_CAPACITY = 4096
_MAX_MERGED_RAW_CHARS = 65_536


@dataclass(slots=True)
class TranscriptSpan:
    """One rendered chunk: global rows, click action, source block, raw ANSI."""

    start_row: int
    end_row: int
    action: object | None
    block: object | None
    raw: str


class ClickSpanRegistry:
    """Map global transcript rows to the sources rendered onto them."""

    def __init__(self, *, capacity: int = _RETENTION_CAPACITY) -> None:
        self._capacity = max(1, int(capacity))
        self._spans: list[TranscriptSpan] = []
        self._dropped_count = 0

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def dropped_count(self) -> int:
        """Return how many retained spans the capacity bound has dropped."""
        return self._dropped_count

    @property
    def spans(self) -> tuple[TranscriptSpan, ...]:
        """Return every retained span in transcript order."""
        return tuple(self._spans)

    def note_dropped(self, count: int) -> None:
        """Carry an earlier drop tally across a reflow rebuild."""
        self._dropped_count += max(0, int(count))

    def record(
        self,
        start_row: int,
        end_row: int,
        action: object | None,
        *,
        block: object | None = None,
        raw: str = "",
    ) -> None:
        """Register one rendered chunk, replacing spans a rewritten tail covers."""
        if end_row < start_row:
            # The chunk only mutated the open tail row (or erased it). Keep its
            # bytes with the span that owns that row so replay stays faithful.
            if raw and self._spans:
                self._spans[-1].raw += raw
            return
        last = self._spans[-1] if self._spans else None
        if last is not None and self._continues(last, start_row, action, block, raw):
            # Chunks flushed while rendering one block share a source.
            last.end_row = max(last.end_row, end_row)
            last.raw += raw
            return
        while self._spans and self._spans[-1].end_row >= start_row:
            self._spans.pop()
        self._spans.append(TranscriptSpan(start_row, end_row, action, block, raw))
        overflow = len(self._spans) - self._capacity
        if overflow > 0:
            del self._spans[:overflow]
            self._dropped_count += overflow

    @staticmethod
    def _continues(
        last: TranscriptSpan,
        start_row: int,
        action: object | None,
        block: object | None,
        raw: str,
    ) -> bool:
        if last.action is not action or last.block is not block:
            return False
        if last.end_row < start_row - 1:
            return False
        # Untagged raw runs merge so replays stay ordered, but each merged
        # entry stays bounded; block chunks are bounded by the block itself.
        return block is not None or len(last.raw) + len(raw) <= _MAX_MERGED_RAW_CHARS

    def action_at(self, row: int) -> object | None:
        """Return the action registered for one global transcript row."""
        for span in reversed(self._spans):
            if span.end_row < row:
                return None
            if span.start_row <= row:
                return span.action
        return None


__all__ = ["ClickSpanRegistry", "TranscriptSpan"]
