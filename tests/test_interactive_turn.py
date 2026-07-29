from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_app_cli.runtime.interactive_turn import InteractiveTurnBindings
from amplifier_app_cli.runtime.interactive_turn import InteractiveTurnConfig
from amplifier_app_cli.runtime.interactive_turn import InteractiveTurnRunner
from amplifier_app_cli.runtime.interactive_turn import InteractiveTurnServices
from amplifier_app_cli.runtime.session_events import PROMPT_COMPLETE
from amplifier_app_cli.ui.evidence_links import EvidenceLinkModel
from amplifier_app_cli.ui.git_yield import GitDiffSnapshot
from amplifier_app_cli.ui.interaction_state import SteeringQueue
from amplifier_app_cli.ui.outcome_ledger import OutcomeLedger


class _Cancellation:
    is_cancelled = False
    is_immediate = False

    def reset(self) -> None:
        self.is_cancelled = False
        self.is_immediate = False


@pytest.mark.asyncio
async def test_success_emits_prompt_complete_without_layered_app(
    tmp_path: Path,
) -> None:
    emitted: list[str] = []
    hooks = MagicMock()

    async def emit(event: str, data: dict[str, object]) -> None:
        emitted.append(event)

    hooks.emit = AsyncMock(side_effect=emit)
    persist = AsyncMock()
    render = MagicMock()
    completion = MagicMock()
    events = MagicMock()
    running: list[bool] = []
    titles: list[str | None] = []

    async def execute(prompt: str) -> str:
        return "answer"

    async def repair() -> bool:
        return False

    async def capture(path: Path) -> GitDiffSnapshot:
        return GitDiffSnapshot(True)

    runner = InteractiveTurnRunner(
        config=InteractiveTurnConfig("session-1", tmp_path),
        services=InteractiveTurnServices(
            execute=execute,
            cancellation=_Cancellation(),
            get_hooks=lambda: hooks,
            repair_transcript=repair,
            persist=persist,
            render_message=render,
            capture_diff=capture,
            events=events,
            outcome_ledger=OutcomeLedger(),
            completion=completion,
            evidence=EvidenceLinkModel(),
        ),
        bindings=InteractiveTurnBindings(
            immediate_interrupt=asyncio.Event(),
            request_interrupt=lambda: True,
            summarize=lambda text, **kwargs: text,
            set_running=running.append,
            set_task_title=titles.append,
            refresh_title=lambda title, active: None,
            get_layered_app=lambda: None,
            active_mode=lambda: "chat",
            enqueue_followup=lambda prompt: None,
            notify=lambda text: None,
            steering_queue=SteeringQueue(),
        ),
    )

    assert await runner.execute("hello") is True

    assert PROMPT_COMPLETE in emitted
    render.assert_called_once()
    persist.assert_awaited_once()
    completion.render.assert_called_once()
    assert running == [True, False]
    assert titles == ["hello", None]
