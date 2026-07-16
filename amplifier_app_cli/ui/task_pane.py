"""Formatting for the layered task-status pane."""

from __future__ import annotations

from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.utils import get_cwidth

from .task_status import TaskStatus
from .task_status import TaskStatusTracker
from .task_status import TaskTreeRow


def format_task_pane_text(
    *,
    tracker: TaskStatusTracker | None,
    session_id: str | None,
    is_running: bool,
    max_lines: int = 16,
    max_columns: int = 96,
) -> FormattedText:
    """Render root todos and delegated sessions within a fixed line budget."""
    max_lines = max(4, max_lines)
    max_columns = max(20, max_columns)
    todos = tracker.todo_snapshot() if tracker is not None else ()
    rows = tracker.tree_rows() if tracker is not None else ()
    completed = sum(todo.status == "completed" for todo in todos)

    todo_limit = min(3, len(todos))
    row_limit = min(8, len(rows))
    show_todo_more = len(todos) > todo_limit
    show_row_more = len(rows) > row_limit

    def line_count() -> int:
        agent_lines = row_limit or 1
        return 3 + todo_limit + int(show_todo_more) + agent_lines + int(show_row_more)

    while line_count() > max_lines and todo_limit > 1:
        todo_limit -= 1
        show_todo_more = len(todos) > todo_limit
    if line_count() > max_lines and show_todo_more:
        show_todo_more = False
    while line_count() > max_lines and row_limit > 1:
        row_limit -= 1
        show_row_more = len(rows) > row_limit
    if line_count() > max_lines and show_row_more:
        show_row_more = False
    while line_count() > max_lines and todo_limit:
        todo_limit -= 1

    fragments: list[tuple[str, str]] = [
        ("class:tasks.title", f" Tasks  Plan {completed}/{len(todos)}\n"),
    ]
    for todo in todos[:todo_limit]:
        marker, style = {
            "completed": ("✔", "class:tasks.completed"),
            "in_progress": ("■", "class:tasks.running"),
        }.get(todo.status, ("□", "class:tasks.muted"))
        text = _summary(todo.display_text, min(84, max_columns - 6))
        fragments.append((style, f"  {marker} {text}\n"))
    if show_todo_more:
        fragments.append(
            (
                "class:tasks.muted",
                f"  {_summary(f'... {len(todos) - todo_limit} more', max_columns - 2)}\n",
            )
        )

    fragments.append(("class:tasks.section", " Agents\n"))
    root_status = "working" if is_running else "idle"
    root_id = session_id[:8] if session_id else "new"
    root_style = "class:tasks.running" if is_running else "class:tasks.muted"
    root = _summary(f"{root_id} current session · {root_status}", max_columns - 2)
    fragments.append((root_style, f"  {root}\n"))

    visible_rows = _visible_rows(rows, row_limit)
    for row in visible_rows:
        node = row.node
        status_style = {
            TaskStatus.RUNNING: "class:tasks.running",
            TaskStatus.COMPLETED: "class:tasks.completed",
            TaskStatus.FAILED: "class:tasks.failed",
            TaskStatus.CANCELLED: "class:tasks.muted",
            TaskStatus.INCOMPLETE: "class:tasks.muted",
        }[node.status]
        label = _summary(
            f"{_tree_prefix(row.prefix)}● {node.agent} {node.session_id[:8]}"
            f" · {node.status.value}",
            min(92, max_columns - 2),
        )
        fragments.append((status_style, f"  {label}\n"))
    if not rows:
        fragments.append(("class:tasks.muted", "  No delegated agents\n"))
    elif show_row_more:
        fragments.append(
            (
                "class:tasks.muted",
                f"  {_summary(f'... {len(rows) - row_limit} more agents', max_columns - 2)}\n",
            )
        )
    return FormattedText(fragments)


def _visible_rows(rows: tuple[TaskTreeRow, ...], limit: int) -> tuple[TaskTreeRow, ...]:
    """Prefer running and recently updated nodes while retaining their ancestry."""
    if len(rows) <= limit:
        return rows
    by_id = {row.node.session_id: row for row in rows}
    priority = sorted(
        rows,
        key=lambda row: (
            row.node.status == TaskStatus.RUNNING,
            row.node.updated_at,
            row.node.order,
        ),
        reverse=True,
    )
    selected: set[str] = set()
    for row in priority:
        chain = []
        chain_seen: set[str] = set()
        current = row
        while (
            current.node.session_id not in selected
            and current.node.session_id not in chain_seen
        ):
            chain_seen.add(current.node.session_id)
            chain.append(current.node.session_id)
            parent = by_id.get(current.node.parent_id)
            if parent is None:
                break
            current = parent
        missing = [node_id for node_id in reversed(chain) if node_id not in selected]
        if not selected and len(missing) > limit:
            selected.update(missing[-limit:])
            break
        if len(selected) + len(missing) <= limit:
            selected.update(missing)
        if len(selected) >= limit:
            break
    return tuple(row for row in rows if row.node.session_id in selected)


def _tree_prefix(prefix: str) -> str:
    """Map the tracker's ASCII tree prefixes onto the spec glyphs (├─/└─/│)."""
    return prefix.replace("|  ", "│  ").replace("|- ", "├─ ").replace("`- ", "└─ ")


def _summary(text: str, max_cells: int) -> str:
    collapsed = " ".join(str(text).split()).strip() or "chat"
    if get_cwidth(collapsed) <= max_cells:
        return collapsed
    result = ""
    for char in collapsed:
        if get_cwidth(result + char) > max_cells - 3:
            break
        result += char
    return result.rstrip() + "..."


__all__ = ["format_task_pane_text"]
