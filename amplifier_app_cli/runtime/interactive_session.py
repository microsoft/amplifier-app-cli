"""Focused lifecycle for queued interactive session turns."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from amplifier_app_cli.ui.runtime_values import sanitize

_AttachmentT = TypeVar("_AttachmentT")

_PREVIEW_MAX_MESSAGES = 8
_PREVIEW_MAX_CHARS = 80


def _sanitize_preview(value: object) -> str:
    """Collapse whitespace and strip control characters for one-line display."""
    clean = " ".join(sanitize(str(value)).split())
    if len(clean) <= _PREVIEW_MAX_CHARS:
        return clean
    return clean[: _PREVIEW_MAX_CHARS - 1].rstrip() + "…"


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    queued_behind_active_turn: bool
    queued_count: int


class InteractiveSessionRuntime(Generic[_AttachmentT]):
    """Own prompt ordering, one-at-a-time execution, and idle shutdown.

    Waiting work lives in one deque owned by the event loop. The drain task
    pops the active turn from the left *before* executing it, so a right-pop
    (``pop_last_queued``) can only ever remove work the drain task has not
    picked up yet — never the actively executing turn.
    """

    def __init__(
        self,
        *,
        execute_turn: Callable[[str, tuple[_AttachmentT, ...]], Awaitable[bool]],
        on_error: Callable[[Exception], None],
        on_idle_exit: Callable[[], None],
    ) -> None:
        self._execute_turn = execute_turn
        self._on_error = on_error
        self._on_idle_exit = on_idle_exit
        self._waiting: deque[tuple[str, tuple[_AttachmentT, ...]]] = deque()
        self._runner_task: asyncio.Task[None] | None = None
        self._exit_after_idle = False

    @property
    def queued_count(self) -> int:
        return len(self._waiting)

    def queued_preview(self) -> tuple[str, ...]:
        """Frozen, sanitized snapshot of waiting prompt texts for the UI."""
        waiting = tuple(self._waiting)[:_PREVIEW_MAX_MESSAGES]
        return tuple(_sanitize_preview(prompt) for prompt, _ in waiting)

    @property
    def active(self) -> bool:
        return self._runner_task is not None and not self._runner_task.done()

    async def enqueue(
        self,
        prompt: str,
        attachments: tuple[_AttachmentT, ...] = (),
    ) -> EnqueueResult:
        queued_behind_active_turn = self.active
        self._waiting.append((prompt, attachments))
        queued_count = len(self._waiting)
        self._ensure_runner()
        return EnqueueResult(queued_behind_active_turn, queued_count)

    def enqueue_next(
        self,
        prompt: str,
        attachments: tuple[_AttachmentT, ...] = (),
    ) -> None:
        """Append follow-up work from inside the active turn."""
        self._waiting.append((prompt, attachments))
        self._ensure_runner()

    def pop_last_queued(self) -> tuple[str, tuple[_AttachmentT, ...]] | None:
        """Remove and return the newest waiting prompt (spec queued-bar edit).

        Returns ``None`` when nothing is waiting. The actively executing turn
        was already popped by the drain task, so it can never be recalled;
        both sides mutate the deque only from the owning event loop.
        """
        if not self._waiting:
            return None
        return self._waiting.pop()

    def request_exit(self) -> bool:
        """Exit now when idle, otherwise arrange exit after queued work."""
        if self.active or self.queued_count:
            self._exit_after_idle = True
            return False
        self._on_idle_exit()
        return True

    async def wait(self) -> None:
        """Wait until the current runner and any race-appended work finish."""
        while self._runner_task is not None:
            task = self._runner_task
            await task
            if self._runner_task is task:
                return

    def _ensure_runner(self) -> None:
        if not self.active:
            self._runner_task = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        try:
            while self._waiting:
                prompt, attachments = self._waiting.popleft()
                try:
                    await self._execute_turn(prompt, attachments)
                except Exception as error:
                    self._on_error(error)
        finally:
            self._runner_task = None
            if self._waiting:
                self._ensure_runner()
            elif self._exit_after_idle:
                self._on_idle_exit()


__all__ = ["EnqueueResult", "InteractiveSessionRuntime"]
