from __future__ import annotations

import asyncio

import pytest
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from amplifier_app_cli.ui.clipboard_availability import ClipboardAvailability
from amplifier_app_cli.ui.clipboard_availability import (
    ClipboardImageAvailabilityDetector,
)
from amplifier_app_cli.ui.command_registry import CommandRegistry
from amplifier_app_cli.ui.layered_repl import LayeredReplApp
from amplifier_app_cli.ui.layered_repl import LayeredReplBindings
from amplifier_app_cli.ui.layered_repl import LayeredReplCompletion
from amplifier_app_cli.ui.layered_repl import LayeredReplConfig
from amplifier_app_cli.ui.layered_repl import LayeredReplServices


@pytest.mark.asyncio
async def test_question_mark_reveals_more_shortcuts_only_at_empty_idle_prompt(
    tmp_path,
) -> None:
    detector = ClipboardImageAvailabilityDetector(
        interval_seconds=60,
        probe=lambda: ClipboardAvailability.EMPTY,
    )
    with create_pipe_input() as pipe_input:
        app = LayeredReplApp(
            config=LayeredReplConfig(
                history_path=tmp_path / "history",
                completion=LayeredReplCompletion(
                    CommandRegistry.from_legacy({"/help": {"description": "Show help"}})
                ),
                input=pipe_input,
                output=DummyOutput(),
            ),
            bindings=LayeredReplBindings(on_submit=lambda submission: None),
            services=LayeredReplServices(clipboard_detector=detector),
        )
        task = asyncio.create_task(app.run_async())
        await asyncio.sleep(0.05)

        pipe_input.send_text("?")
        await asyncio.sleep(0.05)

        notice = app._notices.current()
        assert notice is not None
        assert "drag copy" in notice.text
        assert "shift-drag native select" in notice.text
        assert "ctrl-l ledger" in notice.text
        assert "ctrl-y decisions" in notice.text
        assert app.input_buffer.text == ""

        app.input_buffer.insert_text("why")
        pipe_input.send_text("?")
        await asyncio.sleep(0.05)
        assert app.input_buffer.text == "why?"

        app.exit()
        await asyncio.wait_for(task, timeout=1)
