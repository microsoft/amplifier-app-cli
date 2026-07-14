"""Deterministic cleanup for an interactive Amplifier session."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from .cleanup_events import CLEANUP_FINALLY_BEGIN
from .cleanup_events import CLEANUP_FINALLY_END
from .session_access import session_coordinator


class InteractiveSessionCleanup:
    """Own final draining, persistence, kernel cleanup, and UI teardown."""

    def __init__(
        self,
        *,
        session: object,
        session_id: str,
        wait_for_runner: Callable[[], Awaitable[None]],
        persist: Callable[[], Awaitable[None]],
        cleanup_session: Callable[[], Awaitable[None]],
        unregister: tuple[Callable[[], None], ...],
        set_terminal_title: Callable[[str], None],
        get_layered_app: Callable[[], Any | None],
    ) -> None:
        self._coordinator = session_coordinator(session)
        self._session_id = session_id
        self._wait_for_runner = wait_for_runner
        self._persist = persist
        self._cleanup_session = cleanup_session
        self._unregister = unregister
        self._set_terminal_title = set_terminal_title
        self._get_layered_app = get_layered_app

    async def run(self) -> None:
        await self._wait_for_runner()
        await self._persist()
        hooks = self._coordinator.get("hooks")
        if hooks:
            await hooks.emit(
                CLEANUP_FINALLY_BEGIN,
                {"session_id": self._session_id},
            )
        try:
            await self._cleanup_session()
        finally:
            if hooks:
                await hooks.emit(
                    CLEANUP_FINALLY_END,
                    {"session_id": self._session_id},
                )
            for unregister in self._unregister:
                unregister()
            self._set_terminal_title("session exited")
            layered_app = self._get_layered_app()
            if layered_app is not None:
                layered_app.emit_ambient_state(is_running=False, needs_count=0)


__all__ = ["InteractiveSessionCleanup"]
