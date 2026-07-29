from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_app_cli.runtime.transcript_repair import (
    repair_interactive_transcript,
)


@pytest.mark.asyncio
async def test_live_repair_persists_orphaned_tool_result() -> None:
    messages = [
        {"role": "user", "content": "list files"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "bash", "arguments": "{}"},
                    "tool": "bash",
                }
            ],
        },
        {"role": "user", "content": "what happened?"},
    ]
    context = MagicMock()
    context.get_messages = AsyncMock(return_value=messages)
    context.set_messages = AsyncMock()
    coordinator = MagicMock()
    coordinator.get.return_value = context
    session = MagicMock()
    session.coordinator = coordinator
    persist = AsyncMock()

    repaired = await repair_interactive_transcript(session, persist=persist)

    assert repaired is True
    context.set_messages.assert_awaited_once()
    persist.assert_awaited_once()
    saved = context.set_messages.await_args.args[0]
    assert any(
        message.get("role") == "tool" and message.get("tool_call_id") == "call-1"
        for message in saved
    )


@pytest.mark.asyncio
async def test_live_repair_is_noop_without_context() -> None:
    coordinator = MagicMock()
    coordinator.get.return_value = None
    session = MagicMock()
    session.coordinator = coordinator
    persist = AsyncMock()

    repaired = await repair_interactive_transcript(session, persist=persist)

    assert repaired is False
    persist.assert_not_awaited()
