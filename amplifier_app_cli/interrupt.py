"""Shared interactive SIGINT handling for asynchronous CLI work."""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Awaitable
from contextlib import suppress
from typing import Any, TypeVar

_ResultT = TypeVar("_ResultT")


async def run_with_interrupt(
    awaitable: Awaitable[_ResultT],
    *,
    cancellation: Any,
    console: Any,
) -> _ResultT:
    """Await work with the CLI's graceful-then-immediate Ctrl+C behavior.

    The first interrupt updates the coordinator cancellation token, allowing it
    to propagate to registered child sessions. A second interrupt cancels the
    local task immediately. Callers remain responsible for interpreting a
    graceful cancellation after the awaitable returns.
    """

    cancellation.reset()

    def _handle_sigint(_signum: int, _frame: Any) -> None:
        # CancellationToken updates are intentionally synchronous. Scheduling
        # these writes would race when a user presses Ctrl+C twice quickly.
        if cancellation.is_cancelled:
            cancellation.request_immediate()
            console.print("\n[bold red]Cancelling immediately...[/bold red]")
            return

        cancellation.request_graceful()
        running_tools = cancellation.running_tool_names
        if running_tools:
            tools = ", ".join(running_tools)
            console.print(
                "\n[yellow]Stopping after current operation in "
                f"[bold]{tools}[/bold]... (Ctrl+C again to force)[/yellow]"
            )
        else:
            console.print(
                "\n[yellow]Stopping after current operation completes... "
                "(Ctrl+C again to force)[/yellow]"
            )

    task = asyncio.ensure_future(awaitable)
    original_handler = signal.signal(signal.SIGINT, _handle_sigint)
    try:
        while not task.done():
            if cancellation.is_immediate:
                task.cancel()
                break
            await asyncio.sleep(0.05)
        return await task
    finally:
        signal.signal(signal.SIGINT, original_handler)
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
