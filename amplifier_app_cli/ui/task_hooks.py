"""Hook wiring for the layered task-status UI."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .stream_status import suppress_legacy_streaming_ui
from .task_status import TaskStatusTracker

TASK_STATUS_CAPABILITY = "ui.task_status_tracker"


def attach_task_status_hooks(
    coordinator: Any,
    tracker: TaskStatusTracker,
) -> Callable[[], None]:
    """Attach task tracking and suppress duplicate Todo transcript output."""
    coordinator.register_capability(TASK_STATUS_CAPABILITY, tracker)
    hooks = coordinator.get("hooks")
    if not hooks:
        return lambda: None

    unregister_callbacks = [tracker.register_hooks(hooks)]
    hooks.unregister("hooks-todo-display-pre")
    hooks.unregister("hooks-todo-display-post")
    suppress_legacy_streaming_ui(hooks)

    def unregister_all() -> None:
        for unregister in reversed(unregister_callbacks):
            unregister()

    return unregister_all


__all__ = ["TASK_STATUS_CAPABILITY", "attach_task_status_hooks"]
