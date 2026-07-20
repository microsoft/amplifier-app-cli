from __future__ import annotations

from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_app_cli.main import CommandProcessor
from amplifier_app_cli.runtime.interactive_input import InteractiveInputRouter
from amplifier_app_cli.ui.command_registry import CommandRegistry
from amplifier_app_cli.ui.interaction_state import SteeringQueue
from amplifier_app_cli.ui.notices import NoticeKind
from amplifier_app_cli.ui.session_commands import SessionCommandResult


def _router(
    *, running: bool = False, command_processor: Any | None = None
) -> tuple[InteractiveInputRouter, Any, AsyncMock, list[tuple[str, NoticeKind]]]:
    commands = command_processor if command_processor is not None else MagicMock()
    session_commands = MagicMock()
    interaction = MagicMock()
    interaction.active_mode.return_value = "chat"
    events = MagicMock()
    enqueue = AsyncMock()
    notices: list[tuple[str, NoticeKind]] = []
    router = InteractiveInputRouter(
        command_processor=commands,
        session_commands=session_commands,
        interaction=interaction,
        steering_queue=SteeringQueue(),
        events=events,
        active_mode=lambda: "chat",
        is_running=lambda: running,
        expand_prompt=AsyncMock(side_effect=lambda text: f"expanded:{text}"),
        enqueue_prompt=enqueue,
        notify=lambda text, kind: notices.append((text, kind)),
        get_layered_app=lambda: None,
        summarize=lambda text, **kwargs: text,
    )
    return router, commands, enqueue, notices


@pytest.mark.asyncio
async def test_prompt_is_expanded_and_enqueued_losslessly() -> None:
    router, commands, enqueue, notices = _router()
    commands.process_input.return_value = ("prompt", {"text": "hello\nworld"})

    assert await router.handle("hello\nworld") is True

    enqueue.assert_awaited_once_with("expanded:hello\nworld", ())
    assert notices == []


@pytest.mark.asyncio
async def test_running_prompt_becomes_a_steer() -> None:
    router, commands, enqueue, notices = _router(running=True)
    commands.process_input.return_value = ("prompt", {"text": "change direction"})

    assert await router.handle("change direction") is True

    enqueue.assert_not_awaited()
    assert notices == [("steer queued · expanded:change direction", NoticeKind.INFO)]


@pytest.mark.asyncio
async def test_exit_is_not_dispatched() -> None:
    router, commands, enqueue, notices = _router()

    assert await router.handle("quit") is False
    commands.process_input.assert_not_called()
    enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_interactive_skill_execution_uses_registered_handler_metadata() -> None:
    session = MagicMock()
    session.coordinator.session_state = {"active_mode": None}
    session.coordinator.get_capability.return_value = None
    processor = CommandProcessor(session, "foundation")
    original = processor.COMMAND_REGISTRY.require("/skill")
    replacement = replace(original, handler="_route_probe")
    processor.COMMAND_REGISTRY = CommandRegistry(
        replacement if spec is original else spec
        for spec in processor.COMMAND_REGISTRY.specs
    )
    route_probe = AsyncMock(
        return_value=SessionCommandResult(prompt="registry-selected prompt")
    )
    private_loader = AsyncMock(side_effect=AssertionError("router bypassed registry"))
    setattr(processor, "_route_probe", route_probe)
    setattr(processor, "_load_skill", private_loader)
    router, _, enqueue, _ = _router(command_processor=processor)

    assert await router.handle("/skill simplify") is True

    route_probe.assert_awaited_once()
    private_loader.assert_not_awaited()
    enqueue.assert_awaited_once_with("expanded:registry-selected prompt", ())
