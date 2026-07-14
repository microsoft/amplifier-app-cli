from __future__ import annotations

import asyncio

import pytest

from amplifier_app_cli.runtime.turn_execution import await_turn_or_interrupt


@pytest.mark.asyncio
async def test_turn_returns_when_execution_finishes() -> None:
    interrupt = asyncio.Event()
    execute_task = asyncio.create_task(asyncio.sleep(0, result="done"))

    result = await await_turn_or_interrupt(
        execute_task,
        interrupt,
        is_immediate=lambda: False,
    )

    assert result == "done"


@pytest.mark.asyncio
async def test_immediate_interrupt_cancels_execution() -> None:
    interrupt = asyncio.Event()
    started = asyncio.Event()

    async def execute() -> str:
        started.set()
        await asyncio.Event().wait()
        return "unreachable"

    execute_task = asyncio.create_task(execute())
    waiter = asyncio.create_task(
        await_turn_or_interrupt(
            execute_task,
            interrupt,
            is_immediate=lambda: True,
        )
    )
    await started.wait()
    interrupt.set()

    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert execute_task.cancelled()


@pytest.mark.asyncio
async def test_completed_turn_wins_a_simultaneous_interrupt() -> None:
    interrupt = asyncio.Event()
    execute_task = asyncio.create_task(asyncio.sleep(0, result="answer"))
    await execute_task
    assert execute_task.done()
    interrupt.set()

    result = await await_turn_or_interrupt(
        execute_task,
        interrupt,
        is_immediate=lambda: True,
    )

    assert result == "answer"
