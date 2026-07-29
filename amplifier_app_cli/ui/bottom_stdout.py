"""Single-owner output plumbing for the full-screen transcript."""

from __future__ import annotations

import sys
from collections.abc import Callable
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol
from threading import RLock


class _TerminalStream(Protocol):
    def fileno(self) -> int: ...

    def isatty(self) -> bool: ...


class TranscriptOutput:
    """File-like stream that commits complete writes to a transcript sink."""

    def __init__(
        self,
        sink: Callable[[str], None],
        stream: _TerminalStream | None = None,
    ) -> None:
        self._sink = sink
        fallback = stream if stream is not None else sys.__stdout__
        self._stream: _TerminalStream = fallback if fallback is not None else sys.stdout
        self._buffer: list[str] = []
        self._batch_depth = 0
        self._lock = RLock()

    def write(self, data: str) -> int:
        value = str(data)
        with self._lock:
            self._buffer.append(value)
        return len(value)

    def flush(self) -> None:
        with self._lock:
            if self._batch_depth:
                return
            text = "".join(self._buffer)
            self._buffer.clear()
        if text:
            self._sink(text)

    @contextmanager
    def batch(self) -> Iterator[TranscriptOutput]:
        """Commit nested writes as one transcript chunk at the outer boundary."""
        with self._lock:
            self._batch_depth += 1
        try:
            yield self
        finally:
            with self._lock:
                self._batch_depth -= 1
                should_flush = self._batch_depth == 0
            if should_flush:
                self.flush()

    def fileno(self) -> int:
        return self._stream.fileno()

    def isatty(self) -> bool:
        return bool(self._stream.isatty())

    @property
    def encoding(self) -> str:
        return getattr(self._stream, "encoding", None) or "utf-8"

    @property
    def errors(self) -> str:
        return getattr(self._stream, "errors", None) or "strict"


class TranscriptOutputBridge:
    """Route process-level stdout/stderr through the transcript while active."""

    def __init__(self, sink: Callable[[str], None]) -> None:
        self.output = TranscriptOutput(sink)
        self._depth = 0
        self._stdout = sys.stdout
        self._stderr = sys.stderr
        self._lock = RLock()

    @property
    def active(self) -> bool:
        return self._depth > 0

    @contextmanager
    def patch(self) -> Iterator[TranscriptOutput]:
        with self._lock:
            if self._depth == 0:
                self._stdout = sys.stdout
                self._stderr = sys.stderr
                sys.stdout = self.output  # type: ignore[assignment]
                sys.stderr = self.output  # type: ignore[assignment]
            self._depth += 1
        try:
            yield self.output
        finally:
            self.output.flush()
            with self._lock:
                self._depth -= 1
                if self._depth == 0:
                    sys.stdout = self._stdout
                    sys.stderr = self._stderr


__all__ = [
    "TranscriptOutput",
    "TranscriptOutputBridge",
]
