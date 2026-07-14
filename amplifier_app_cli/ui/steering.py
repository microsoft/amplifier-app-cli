"""Bounded mid-turn steering queue for interactive sessions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic

_MAX_STEERS = 32
_MAX_STEER_TEXT = 32_768


def _safe_multiline(value: object, limit: int) -> str:
    return "".join(
        character
        for character in str(value)
        if character in {"\n", "\t"} or ord(character) >= 32
    )[:limit]


@dataclass(frozen=True, slots=True)
class QueuedSteer:
    steer_id: str
    text: str
    created_at: float
    display_text: str | None = None


class SteeringQueue:
    """Queue user steering for consumption at orchestration step boundaries."""

    def __init__(self, *, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._next_id = 1
        self._pending: list[QueuedSteer] = []
        self._listeners: list[Callable[[], None]] = []

    @property
    def pending(self) -> tuple[QueuedSteer, ...]:
        return tuple(self._pending)

    def enqueue(
        self, text: object, *, display_text: object | None = None
    ) -> QueuedSteer:
        if len(self._pending) >= _MAX_STEERS:
            raise ValueError("steering queue limit reached")
        clean = _safe_multiline(text, _MAX_STEER_TEXT)
        if not clean.strip():
            raise ValueError("steering text cannot be empty")
        clean_display = (
            _safe_multiline(display_text, _MAX_STEER_TEXT)
            if display_text is not None
            else None
        )
        if clean_display == clean:
            clean_display = None
        steer = QueuedSteer(
            f"steer-{self._next_id}",
            clean,
            self._clock(),
            display_text=clean_display,
        )
        self._next_id += 1
        self._pending.append(steer)
        self._notify()
        return steer

    def consume_next(self) -> QueuedSteer | None:
        if not self._pending:
            return None
        steer = self._pending.pop(0)
        self._notify()
        return steer

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            listener()


__all__ = ["QueuedSteer", "SteeringQueue"]
