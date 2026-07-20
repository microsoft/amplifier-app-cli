"""Synchronous graceful/immediate cancellation escalation for the TUI."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Protocol

from amplifier_app_cli.ui.notices import NoticeKind


class _Cancellation(Protocol):
    @property
    def is_cancelled(self) -> bool: ...

    @property
    def running_tool_names(self) -> list[str]: ...

    def request_graceful(self) -> bool: ...

    def request_immediate(self) -> bool: ...


class ExecutionInterruptController:
    """Escalate the first interrupt gracefully and the second immediately."""

    def __init__(
        self,
        *,
        cancellation: _Cancellation,
        is_running: Callable[[], bool],
        immediate_event: asyncio.Event,
        notify: Callable[[str, NoticeKind], None],
    ) -> None:
        self._cancellation = cancellation
        self._is_running = is_running
        self._immediate_event = immediate_event
        self._notify = notify

    def request(self) -> bool:
        if not self._is_running():
            return False
        if self._cancellation.is_cancelled:
            self._cancellation.request_immediate()
            self._immediate_event.set()
            self._notify("cancelling immediately", NoticeKind.ERROR)
            return True

        self._cancellation.request_graceful()
        running_tools = self._cancellation.running_tool_names
        if running_tools:
            tools = ", ".join(running_tools)
            message = f"stopping after {tools} · interrupt again to force"
        else:
            message = "stopping after current operation · interrupt again to force"
        self._notify(message, NoticeKind.WARNING)
        return True


__all__ = ["ExecutionInterruptController"]
