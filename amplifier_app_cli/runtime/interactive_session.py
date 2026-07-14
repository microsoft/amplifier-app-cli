"""Focused lifecycle for queued interactive session turns."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, TypeVar


_AttachmentT = TypeVar("_AttachmentT")


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    queued_behind_active_turn: bool
    queued_count: int


class InteractiveSessionRuntime(Generic[_AttachmentT]):
    """Own prompt ordering, one-at-a-time execution, and idle shutdown."""

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
        self._queue: asyncio.Queue[tuple[str, tuple[_AttachmentT, ...]]] = (
            asyncio.Queue()
        )
        self._runner_task: asyncio.Task[None] | None = None
        self._exit_after_idle = False

    @property
    def queued_count(self) -> int:
        return self._queue.qsize()

    @property
    def active(self) -> bool:
        return self._runner_task is not None and not self._runner_task.done()

    async def enqueue(
        self,
        prompt: str,
        attachments: tuple[_AttachmentT, ...] = (),
    ) -> EnqueueResult:
        queued_behind_active_turn = self.active
        await self._queue.put((prompt, attachments))
        queued_count = self._queue.qsize()
        self._ensure_runner()
        return EnqueueResult(queued_behind_active_turn, queued_count)

    def enqueue_next(
        self,
        prompt: str,
        attachments: tuple[_AttachmentT, ...] = (),
    ) -> None:
        """Append follow-up work from inside the active turn."""
        self._queue.put_nowait((prompt, attachments))
        self._ensure_runner()

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
            while True:
                try:
                    prompt, attachments = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                try:
                    await self._execute_turn(prompt, attachments)
                except Exception as error:
                    self._on_error(error)
                finally:
                    self._queue.task_done()
        finally:
            self._runner_task = None
            if not self._queue.empty():
                self._ensure_runner()
            elif self._exit_after_idle:
                self._on_idle_exit()


__all__ = ["EnqueueResult", "InteractiveSessionRuntime"]
