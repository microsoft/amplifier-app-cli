"""Bounded transient notices displayed immediately above the TUI footer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from time import monotonic

_DEFAULT_DURATION_SECONDS = 4.0
_MAX_DURATION_SECONDS = 30.0
_MAX_NOTICE_CHARS = 240


class NoticeKind(str, Enum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class TransientNotice:
    text: str
    kind: NoticeKind
    created_at: float
    expires_at: float


class TransientNoticeState:
    """Hold the latest ephemeral notice and notify layout listeners."""

    def __init__(self, *, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._notice: TransientNotice | None = None
        self._listeners: list[Callable[[], None]] = []

    def show(
        self,
        text: object,
        *,
        kind: NoticeKind = NoticeKind.INFO,
        duration_seconds: float = _DEFAULT_DURATION_SECONDS,
    ) -> TransientNotice:
        if not 0 < duration_seconds <= _MAX_DURATION_SECONDS:
            raise ValueError("duration_seconds must be between 0 and 30")
        clean = _clean_notice_text(text)
        if not clean:
            raise ValueError("notice text cannot be empty")
        now = self._clock()
        notice = TransientNotice(clean, kind, now, now + duration_seconds)
        self._notice = notice
        self._notify()
        return notice

    def current(self) -> TransientNotice | None:
        notice = self._notice
        if notice is not None and self._clock() >= notice.expires_at:
            self._notice = None
            self._notify()
            return None
        return notice

    def clear(self) -> None:
        if self._notice is None:
            return
        self._notice = None
        self._notify()

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            listener()


def _clean_notice_text(value: object) -> str:
    clean = " ".join(
        "".join(character for character in str(value) if ord(character) >= 32).split()
    )
    return clean[:_MAX_NOTICE_CHARS]


__all__ = ["NoticeKind", "TransientNotice", "TransientNoticeState"]
