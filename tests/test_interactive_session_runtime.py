from __future__ import annotations

import asyncio

import pytest

from amplifier_app_cli.runtime.interactive_session import InteractiveSessionRuntime


@pytest.mark.asyncio
async def test_runtime_serializes_prompts_and_reports_queueing() -> None:
    release_first = asyncio.Event()
    started_first = asyncio.Event()
    calls: list[tuple[str, tuple[str, ...]]] = []

    async def execute(prompt: str, attachments: tuple[str, ...]) -> bool:
        calls.append((prompt, attachments))
        if prompt == "first":
            started_first.set()
            await release_first.wait()
        return True

    runtime = InteractiveSessionRuntime[str](
        execute_turn=execute,
        on_error=lambda error: None,
        on_idle_exit=lambda: None,
    )

    first = await runtime.enqueue("first", ("image",))
    await started_first.wait()
    second = await runtime.enqueue("second")
    release_first.set()
    await runtime.wait()

    assert first.queued_behind_active_turn is False
    assert second.queued_behind_active_turn is True
    assert second.queued_count == 1
    assert calls == [("first", ("image",)), ("second", ())]


@pytest.mark.asyncio
async def test_runtime_reports_error_and_continues_queue() -> None:
    errors: list[str] = []
    calls: list[str] = []

    async def execute(prompt: str, attachments: tuple[str, ...]) -> bool:
        calls.append(prompt)
        if prompt == "bad":
            raise ValueError("failed")
        return True

    runtime = InteractiveSessionRuntime[str](
        execute_turn=execute,
        on_error=lambda error: errors.append(str(error)),
        on_idle_exit=lambda: None,
    )
    await runtime.enqueue("bad")
    await runtime.enqueue("good")
    await runtime.wait()

    assert calls == ["bad", "good"]
    assert errors == ["failed"]


@pytest.mark.asyncio
async def test_runtime_defers_exit_until_idle() -> None:
    release = asyncio.Event()
    exits: list[bool] = []

    async def execute(prompt: str, attachments: tuple[str, ...]) -> bool:
        await release.wait()
        return True

    runtime = InteractiveSessionRuntime[str](
        execute_turn=execute,
        on_error=lambda error: None,
        on_idle_exit=lambda: exits.append(True),
    )
    await runtime.enqueue("work")

    assert runtime.request_exit() is False
    assert exits == []
    release.set()
    await runtime.wait()
    assert exits == [True]


@pytest.mark.asyncio
async def test_runtime_exits_immediately_when_idle() -> None:
    exits: list[bool] = []

    async def execute(prompt: str, attachments: tuple[str, ...]) -> bool:
        return True

    runtime = InteractiveSessionRuntime[str](
        execute_turn=execute,
        on_error=lambda error: None,
        on_idle_exit=lambda: exits.append(True),
    )

    assert runtime.request_exit() is True
    assert exits == [True]
