from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_app_cli.runtime.cleanup_events import CLEANUP_FINALLY_BEGIN
from amplifier_app_cli.runtime.cleanup_events import CLEANUP_FINALLY_END
from amplifier_app_cli.runtime.interactive_cleanup import InteractiveSessionCleanup


@pytest.mark.asyncio
async def test_cleanup_orders_drain_save_kernel_and_unregister() -> None:
    events: list[str] = []
    hooks = MagicMock()

    async def emit(event: str, data: dict[str, str]) -> None:
        events.append(event)

    hooks.emit = AsyncMock(side_effect=emit)
    coordinator = MagicMock()
    coordinator.get.return_value = hooks
    session = MagicMock()
    session.coordinator = coordinator
    app = MagicMock()

    async def drain() -> None:
        events.append("drain")

    async def persist() -> None:
        events.append("persist")

    async def kernel_cleanup() -> None:
        events.append("kernel")

    cleanup = InteractiveSessionCleanup(
        session=session,
        session_id="session-1",
        wait_for_runner=drain,
        persist=persist,
        cleanup_session=kernel_cleanup,
        unregister=(lambda: events.append("unregister"),),
        set_terminal_title=lambda title: events.append(title),
        get_layered_app=lambda: app,
    )

    await cleanup.run()

    assert events == [
        "drain",
        "persist",
        CLEANUP_FINALLY_BEGIN,
        "kernel",
        CLEANUP_FINALLY_END,
        "unregister",
        "session exited",
    ]
    app.emit_ambient_state.assert_called_once_with(
        is_running=False,
        needs_count=0,
    )


@pytest.mark.asyncio
async def test_unregisters_even_when_kernel_cleanup_fails() -> None:
    coordinator = MagicMock()
    coordinator.get.return_value = None
    session = MagicMock()
    session.coordinator = coordinator
    unregistered: list[bool] = []

    async def fail() -> None:
        raise RuntimeError("cleanup failed")

    cleanup = InteractiveSessionCleanup(
        session=session,
        session_id="session-1",
        wait_for_runner=AsyncMock(),
        persist=AsyncMock(),
        cleanup_session=fail,
        unregister=(lambda: unregistered.append(True),),
        set_terminal_title=lambda title: None,
        get_layered_app=lambda: None,
    )

    with pytest.raises(RuntimeError, match="cleanup failed"):
        await cleanup.run()
    assert unregistered == [True]
