"""Synchronize active plan steps with narration and terminal title callbacks."""

from __future__ import annotations

from collections.abc import Callable

from .task_status import TaskStatusTracker


class PlanStepSynchronizer:
    """Emit each active step once while refreshing the title on every change."""

    def __init__(
        self,
        tracker: TaskStatusTracker,
        *,
        on_step: Callable[[str], None],
        on_title: Callable[[str | None], None],
    ) -> None:
        self._tracker = tracker
        self._on_step = on_step
        self._on_title = on_title
        self._last_active: str | None = None
        self._remove_listener = tracker.add_listener(self._changed)

    def close(self) -> None:
        self._remove_listener()

    def _changed(self) -> None:
        active = self._tracker.active_step_text()
        if active and active != self._last_active:
            self._on_step(active)
        self._last_active = active
        self._on_title(active)


__all__ = ["PlanStepSynchronizer"]
