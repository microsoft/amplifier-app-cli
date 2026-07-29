"""Event-driven waiting for an interactive session turn."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TypeVar


_T = TypeVar("_T")


async def await_turn_or_interrupt(
    execute_task: asyncio.Task[_T],
    immediate_interrupt: asyncio.Event,
    *,
    is_immediate: Callable[[], bool],
) -> _T:
    """Await a turn without polling and cancel it on an immediate interrupt."""
    interrupt_task = asyncio.create_task(immediate_interrupt.wait())
    try:
        done, _ = await asyncio.wait(
            {execute_task, interrupt_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if execute_task not in done and is_immediate():
            execute_task.cancel()
        return await execute_task
    finally:
        interrupt_task.cancel()
        await asyncio.gather(interrupt_task, return_exceptions=True)


__all__ = ["await_turn_or_interrupt"]
