"""Bounded values used by the interactive task tracker."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

MAX_TODOS = 100
MAX_TASK_TEXT_CHARS = 512


@dataclass(frozen=True, slots=True)
class TodoItem:
    content: str
    active_form: str
    status: str

    @property
    def display_text(self) -> str:
        if self.status == "in_progress":
            return self.active_form or self.content
        return self.content


@dataclass(frozen=True, slots=True)
class PlanSnapshot:
    """Immutable root-plan state suitable for transcript and title rendering."""

    items: tuple[TodoItem, ...]

    @property
    def completed_count(self) -> int:
        return sum(item.status == "completed" for item in self.items)

    @property
    def active_item(self) -> TodoItem | None:
        return next((item for item in self.items if item.status == "in_progress"), None)

    @property
    def active_text(self) -> str | None:
        item = self.active_item
        return item.display_text if item is not None else None


def normalize_todos(todos: Iterable[Any]) -> tuple[TodoItem, ...]:
    normalized = []
    for raw in todos:
        if len(normalized) >= MAX_TODOS:
            break
        item = raw if isinstance(raw, Mapping) else {}
        if not item:
            continue
        content = _clean(item.get("content"))
        active_form = _clean(
            item.get("activeForm") or item.get("active_form") or content
        )
        status = str(item.get("status") or "pending").strip().lower()
        if status not in {"pending", "in_progress", "completed"}:
            status = "pending"
        normalized.append(TodoItem(content, active_form, status))
    return tuple(normalized)


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())[:MAX_TASK_TEXT_CHARS]


__all__ = ["MAX_TASK_TEXT_CHARS", "PlanSnapshot", "TodoItem", "normalize_todos"]
