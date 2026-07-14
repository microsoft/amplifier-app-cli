"""Acceptance coverage for lossless paste routing into Amplifier execution."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

_MAIN = "amplifier_app_cli.main"


def _session() -> MagicMock:
    context = MagicMock()
    context.get_messages = AsyncMock(return_value=[])

    def coordinator_get(name: str):
        if name == "context":
            return context
        if name == "providers":
            return {}
        return None

    coordinator = MagicMock()
    coordinator.get = coordinator_get
    coordinator.get_capability.return_value = None
    coordinator.session_state = {}
    coordinator.todo_state = None
    coordinator.cancellation.is_cancelled = False
    coordinator.cancellation.is_immediate = False
    coordinator.cancellation.running_tool_names = []

    session = MagicMock()
    session.session_id = "paste-acceptance-session"
    session.coordinator = coordinator
    session.config = {}
    return session


def _initialized(session: MagicMock) -> MagicMock:
    initialized = MagicMock()
    initialized.session = session
    initialized.session_id = session.session_id
    initialized.configurator = None
    initialized.cleanup = AsyncMock()
    return initialized


async def _wait_for_stub(app: object, expected: str) -> None:
    for _ in range(100):
        visible = app._visible_editor_text(app.input_buffer.text)  # type: ignore[attr-defined]
        if visible == expected:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("bracketed paste did not collapse into the expected stub")


@pytest.mark.asyncio
async def test_430_line_bracketed_paste_reaches_session_execute_exactly(
    tmp_path: Path,
) -> None:
    from amplifier_app_cli.main import interactive_chat
    from amplifier_app_cli.ui.layered_repl import LayeredReplApp

    raw = "\n".join(f"line {index:03d} · payload {index * 17}" for index in range(430))
    assert len(raw.splitlines()) == 430

    boundary_prompts: list[str] = []
    boundary_reached = asyncio.Event()

    async def execute(prompt: str) -> str:
        boundary_prompts.append(prompt)
        boundary_reached.set()
        return "accepted"

    session = _session()
    session.execute = AsyncMock(side_effect=execute)
    initialized = _initialized(session)
    app_ready = asyncio.Event()
    app_holder: dict[str, LayeredReplApp] = {}

    with create_pipe_input() as pipe_input:

        def create_test_app(*, config, bindings, services) -> LayeredReplApp:
            app = LayeredReplApp(
                config=replace(config, input=pipe_input, output=DummyOutput()),
                bindings=bindings,
                services=services,
            )
            app_holder["app"] = app
            app_ready.set()
            return app

        prompt_session = MagicMock()
        with (
            patch(
                f"{_MAIN}.create_initialized_session",
                new=AsyncMock(return_value=initialized),
            ),
            patch(f"{_MAIN}.supports_layered_ui", return_value=True),
            patch(f"{_MAIN}._create_prompt_session", return_value=prompt_session),
            patch(
                "amplifier_app_cli.ui.layered_repl.LayeredReplApp",
                new=create_test_app,
            ),
            patch("amplifier_app_cli.incremental_save.register_incremental_save"),
            patch(f"{_MAIN}.SessionStore") as store_type,
            patch(f"{_MAIN}.console"),
            patch(
                f"{_MAIN}._process_runtime_mentions",
                new=AsyncMock(side_effect=lambda _session, text: text),
            ),
            patch("amplifier_app_cli.ui.render_message"),
        ):
            store_type.return_value.get_metadata.return_value = {}
            chat_task = asyncio.create_task(
                interactive_chat(
                    config={},
                    search_paths=[tmp_path],
                    verbose=False,
                    bundle_name="test-bundle",
                )
            )

            await asyncio.wait_for(app_ready.wait(), timeout=2)
            app = app_holder["app"]
            pipe_input.send_bytes(b"\x1b[200~" + raw.encode("utf-8") + b"\x1b[201~")
            await _wait_for_stub(app, "[Pasted #1 · 430 lines]")
            pipe_input.send_text("\r")

            await asyncio.wait_for(boundary_reached.wait(), timeout=2)
            app.exit()
            await asyncio.wait_for(chat_task, timeout=5)

    session.execute.assert_awaited_once_with(raw)
    assert boundary_prompts == [raw]
    assert boundary_prompts[0].encode("utf-8") == raw.encode("utf-8")
    assert "[Pasted #1" not in boundary_prompts[0]
