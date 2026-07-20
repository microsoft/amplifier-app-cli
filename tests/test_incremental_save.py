"""Tests for incremental transcript persistence."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_app_cli.incremental_save import IncrementalSaveHook


@pytest.mark.asyncio
async def test_incremental_save_treats_missing_metadata_as_empty():
    context = MagicMock()
    context.get_messages = AsyncMock(
        return_value=[
            {"role": "user", "content": "Run a tool"},
            {"role": "assistant", "content": "Done"},
        ]
    )

    session = MagicMock()
    session.coordinator.get.return_value = context

    store = MagicMock()
    store.get_metadata.side_effect = FileNotFoundError("missing")

    hook = IncrementalSaveHook(
        session=session,
        store=store,
        session_id="test-session",
        bundle_name="foundation",
        config={"providers": [{"config": {"default_model": "gpt-5.5"}}]},
    )

    result = await hook.on_tool_post("tool:post", {"tool_name": "bash"})

    assert result.action == "continue"
    store.save.assert_called_once()
    session_id, messages, metadata = store.save.call_args.args
    assert session_id == "test-session"
    assert len(messages) == 2
    assert metadata["session_id"] == "test-session"
    assert metadata["model"] == "gpt-5.5"
