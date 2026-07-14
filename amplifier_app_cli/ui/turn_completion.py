"""Render a completed turn and apply deterministic mode transitions."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .interaction_controller import InteractionController
from .outcome_ledger import TurnOutcome
from .transcript_blocks import RecapBlock
from .transcript_blocks import Telemetry
from .transcript_blocks import TurnTerminatorBlock
from .ui_events import UiEventDispatcher


class TurnCompletionRenderer:
    def __init__(
        self,
        *,
        events: UiEventDispatcher,
        interaction: InteractionController,
        current_task: Callable[[], str | None],
        get_layered_app: Callable[[], Any | None],
    ) -> None:
        self._events = events
        self._interaction = interaction
        self._current_task = current_task
        self._get_layered_app = get_layered_app

    def render(self, outcome: TurnOutcome) -> None:
        if outcome.interrupted:
            self._events.emit(
                RecapBlock(
                    goal=self._current_task() or "the current task",
                    next_action="resume or provide a new direction",
                )
            )
        self._events.emit(
            TurnTerminatorBlock(
                Telemetry(
                    elapsed_seconds=outcome.elapsed_seconds,
                    tokens=outcome.tokens,
                    cached_percent=outcome.cached_percent,
                    cost=outcome.cost,
                ),
                outcome=outcome.yield_summary,
            )
        )
        completed_mode = self._interaction.active_mode()
        if not outcome.interrupted and completed_mode == "brainstorm":
            self._events.emit(
                RecapBlock(
                    goal="explore the idea",
                    next_action="use /plan to converge",
                )
            )
        elif not outcome.interrupted and completed_mode == "plan":
            self._interaction.activate_local("build")
            self._events.emit(
                RecapBlock(
                    goal="complete the implementation plan",
                    next_action="continue in build mode",
                )
            )
        app = self._get_layered_app()
        if app is not None:
            summary = outcome.yield_summary or (
                "interrupted" if outcome.interrupted else "answer"
            )
            app.notify_turn_complete(summary)


__all__ = ["TurnCompletionRenderer"]
