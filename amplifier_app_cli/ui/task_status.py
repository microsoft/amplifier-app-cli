from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol

from amplifier_core import HookResult

from .task_values import MAX_TASK_TEXT_CHARS, PlanSnapshot, TodoItem, normalize_todos

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INCOMPLETE = "incomplete"


@dataclass
class TaskNode:
    session_id: str
    parent_id: str
    agent: str
    status: TaskStatus
    order: int
    started_at: datetime
    updated_at: datetime
    summary: str = ""
    tool_call_id: str = ""
    parallel_group_id: str = ""


@dataclass(frozen=True)
class TaskCounts:
    running: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    incomplete: int = 0

    @property
    def total(self) -> int:
        return sum(
            (self.running, self.completed, self.failed, self.cancelled, self.incomplete)
        )


@dataclass(frozen=True)
class TaskTreeRow:
    prefix: str
    node: TaskNode


class HookRegistry(Protocol):
    def register(
        self,
        event: str,
        handler: Callable[[str, dict[str, Any]], Any],
        *,
        priority: int = 0,
        name: str | None = None,
    ) -> Callable[[], None] | None: ...

    def unregister(self, name: str) -> Any: ...


TodoSource = Callable[[], Iterable[Mapping[str, Any]] | None]
ChangeListener = Callable[[], None]
_MAX_TASK_NODES = 512
_MAX_PENDING_SUMMARIES = 512
_MAX_ID_CHARS = 256


class TaskStatusTracker:
    EVENTS = (
        "tool:pre tool:post delegate:agent_spawned delegate:agent_resumed "
        "delegate:agent_completed delegate:agent_cancelled delegate:error "
        "session:fork session:start session:resume session:end"
    ).split()

    def __init__(
        self,
        root_session_id: str,
        *,
        todo_source: TodoSource | None = None,
    ) -> None:
        self.root_session_id = root_session_id
        self._todo_source = todo_source
        self._todo_cache: tuple[TodoItem, ...] = ()
        self._pending_todos: tuple[TodoItem, ...] | None = None
        self._pending_summaries: dict[str, str] = {}
        self._nodes: dict[str, TaskNode] = {}
        self._listeners: list[ChangeListener] = []
        self._next_order = 0

    def set_todo_source(self, source: TodoSource | None) -> None:
        self._todo_source = source
        self._notify()

    def set_todos(self, todos: Iterable[Mapping[str, Any]]) -> None:
        self._todo_cache = normalize_todos(todos)
        self._notify()

    def todo_snapshot(self) -> tuple[TodoItem, ...]:
        if self._todo_source is None:
            return self._todo_cache
        try:
            current = self._todo_source()
        except Exception:
            logger.debug("Failed to read live todo state", exc_info=True)
            return self._todo_cache
        if current is None:
            return self._todo_cache
        self._todo_cache = normalize_todos(current)
        return self._todo_cache

    def plan_snapshot(self) -> PlanSnapshot:
        """Return immutable plan state for the live plan widget and title."""
        return PlanSnapshot(self.todo_snapshot())

    def active_step_text(self) -> str | None:
        """Return the active plan verb, if the root plan has one."""
        return self.plan_snapshot().active_text

    def add_listener(self, listener: ChangeListener) -> Callable[[], None]:
        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    def register_hooks(
        self, hooks: HookRegistry, *, priority: int = 50
    ) -> Callable[[], None]:
        unregister_callbacks: list[Callable[[], None]] = []
        for event in self.EVENTS:
            unregister = hooks.register(
                event,
                self.handle_event,
                priority=priority,
                name=f"cli-task-status-{event.replace(':', '-')}",
            )
            if callable(unregister):
                unregister_callbacks.append(unregister)

        def unregister_all() -> None:
            for unregister in reversed(unregister_callbacks):
                unregister()

        return unregister_all

    async def handle_event(self, event: str, data: dict[str, Any]) -> HookResult:
        self.consume(event, data)
        return HookResult(action="continue")

    def consume(self, event: str, data: Mapping[str, Any]) -> None:
        if event in {"tool:pre", "tool:post"}:
            self._consume_tool_event(event, data)
            return

        if event in {"delegate:agent_spawned", "session:fork", "session:start"}:
            session_id = _session_id(data)
            parent_id = _parent_id(data) or self.root_session_id
            if not session_id or session_id == self.root_session_id:
                return
            if event == "session:start" and not _parent_id(data):
                return
            self._upsert(
                session_id,
                parent_id=parent_id,
                agent=_agent_name(data, session_id),
                status=TaskStatus.RUNNING,
                summary=self._summary_for(data),
                tool_call_id=_text(data.get("tool_call_id")),
                parallel_group_id=_text(data.get("parallel_group_id")),
                allow_reopen=False,
            )
            return

        if event in {"delegate:agent_resumed", "session:resume"}:
            session_id = _session_id(data)
            if not session_id or session_id == self.root_session_id:
                return
            self._upsert(
                session_id,
                parent_id=_parent_id(data) or self.root_session_id,
                agent=_agent_name(data, session_id),
                status=TaskStatus.RUNNING,
                summary=self._summary_for(data),
                tool_call_id=_text(data.get("tool_call_id")),
                parallel_group_id=_text(data.get("parallel_group_id")),
                allow_reopen=True,
            )
            return

        if event in {
            "delegate:agent_completed",
            "delegate:agent_cancelled",
            "delegate:error",
            "session:end",
        }:
            session_id = _session_id(data)
            if not session_id or session_id == self.root_session_id:
                return
            status = _terminal_status(event, data)
            self._upsert(
                session_id,
                parent_id=_parent_id(data) or self.root_session_id,
                agent=_agent_name(data, session_id),
                status=status,
                summary=self._summary_for(data),
                tool_call_id=_text(data.get("tool_call_id")),
                parallel_group_id=_text(data.get("parallel_group_id")),
                allow_reopen=False,
            )

    def nodes(self) -> tuple[TaskNode, ...]:
        return tuple(sorted(self._nodes.values(), key=lambda node: node.order))

    def counts(self) -> TaskCounts:
        statuses = [node.status for node in self._nodes.values()]
        return TaskCounts(
            running=statuses.count(TaskStatus.RUNNING),
            completed=statuses.count(TaskStatus.COMPLETED),
            failed=statuses.count(TaskStatus.FAILED),
            cancelled=statuses.count(TaskStatus.CANCELLED),
            incomplete=statuses.count(TaskStatus.INCOMPLETE),
        )

    def footer_summary(self) -> str | None:
        parts: list[str] = []
        todos = self.todo_snapshot()
        if todos:
            completed = sum(item.status == "completed" for item in todos)
            parts.append(f"todo {completed}/{len(todos)}")

        counts = self.counts()
        if counts.total:
            agent_parts = []
            if counts.running:
                agent_parts.append(f"{counts.running} running")
            if counts.completed:
                agent_parts.append(f"{counts.completed} done")
            if counts.failed:
                agent_parts.append(f"{counts.failed} failed")
            if counts.cancelled:
                agent_parts.append(f"{counts.cancelled} cancelled")
            if counts.incomplete:
                agent_parts.append(f"{counts.incomplete} incomplete")
            parts.append("agents " + "/".join(agent_parts))
        return " | ".join(parts) if parts else None

    def tree_rows(self) -> tuple[TaskTreeRow, ...]:
        nodes = self.nodes()
        known_ids = {node.session_id for node in nodes}
        children: dict[str, list[TaskNode]] = {}
        for node in nodes:
            parent_id = node.parent_id
            if parent_id not in known_ids and parent_id != self.root_session_id:
                parent_id = self.root_session_id
            children.setdefault(parent_id, []).append(node)

        rows: list[TaskTreeRow] = []
        visited: set[str] = set()

        def visit(parent_id: str, prefix: str) -> None:
            siblings = children.get(parent_id, [])
            for index, node in enumerate(siblings):
                if node.session_id in visited:
                    continue
                visited.add(node.session_id)
                is_last = index == len(siblings) - 1
                rows.append(TaskTreeRow(prefix + ("`- " if is_last else "|- "), node))
                visit(node.session_id, prefix + ("   " if is_last else "|  "))

        visit(self.root_session_id, "")
        for node in nodes:
            if node.session_id not in visited:
                rows.append(TaskTreeRow("`- ", node))
        return tuple(rows)

    def _consume_tool_event(self, event: str, data: Mapping[str, Any]) -> None:
        tool_name = _text(data.get("tool_name") or data.get("tool"))
        tool_input = _as_mapping(data.get("tool_input") or data.get("input"))
        emitting_session_id = _text(data.get("session_id"))

        if (
            tool_name == "todo"
            and emitting_session_id
            and emitting_session_id != self.root_session_id
        ):
            return

        if event == "tool:pre" and tool_name == "todo":
            todos = tool_input.get("todos")
            if isinstance(todos, Iterable) and not isinstance(todos, (str, bytes)):
                self._pending_todos = normalize_todos(todos)
            return

        if event == "tool:pre" and tool_name in {"delegate", "task"}:
            call_id = _text(data.get("tool_call_id"))
            summary = _text(tool_input.get("instruction") or tool_input.get("task"))
            if call_id and summary:
                if len(self._pending_summaries) >= _MAX_PENDING_SUMMARIES:
                    self._pending_summaries.pop(next(iter(self._pending_summaries)))
                self._pending_summaries[call_id] = _clean_text(summary)
            return

        if event != "tool:post":
            return

        output = _tool_output(data)
        if tool_name == "todo":
            todos = output.get("todos")
            if isinstance(todos, Iterable) and not isinstance(todos, (str, bytes)):
                self._todo_cache = normalize_todos(todos)
            elif self._pending_todos is not None:
                self._todo_cache = self._pending_todos
            self._pending_todos = None
            self._notify()
            return

        if tool_name in {"delegate", "task"}:
            session_id = _text(output.get("session_id"))
            raw_status = _text(output.get("status")).lower()
            if session_id and raw_status:
                status = _status_from_value(raw_status)
                if status is not None and status != TaskStatus.RUNNING:
                    current = self._nodes.get(session_id)
                    parent_id = emitting_session_id or getattr(
                        current, "parent_id", self.root_session_id
                    )
                    self._upsert(
                        session_id,
                        parent_id=parent_id,
                        agent=_agent_name(output, session_id),
                        status=status,
                        summary="",
                        tool_call_id=_text(data.get("tool_call_id")),
                        parallel_group_id="",
                        allow_reopen=False,
                    )

    def _summary_for(self, data: Mapping[str, Any]) -> str:
        direct = _text(
            data.get("instruction") or data.get("task") or data.get("summary")
        )
        if direct:
            return _clean_text(direct)
        call_id = _text(data.get("tool_call_id"))
        return self._pending_summaries.pop(call_id, "") if call_id else ""

    def _upsert(
        self,
        session_id: str,
        *,
        parent_id: str,
        agent: str,
        status: TaskStatus,
        summary: str,
        tool_call_id: str,
        parallel_group_id: str,
        allow_reopen: bool,
    ) -> None:
        now = datetime.now(UTC)
        node = self._nodes.get(session_id)
        if node is None:
            if len(self._nodes) >= _MAX_TASK_NODES:
                evictable = next(
                    (
                        item
                        for item in self.nodes()
                        if item.status != TaskStatus.RUNNING
                    ),
                    self.nodes()[0],
                )
                self._nodes.pop(evictable.session_id, None)
            node = TaskNode(
                session_id=session_id,
                parent_id=parent_id,
                agent=agent or "agent",
                status=status,
                order=self._next_order,
                started_at=now,
                updated_at=now,
                summary=summary,
                tool_call_id=tool_call_id,
                parallel_group_id=parallel_group_id,
            )
            self._nodes[session_id] = node
            self._next_order += 1
        else:
            terminal = node.status != TaskStatus.RUNNING
            reopening_blocked = (
                status == TaskStatus.RUNNING and terminal and not allow_reopen
            )
            uncertainty_preserved = (
                node.status == TaskStatus.INCOMPLETE and status == TaskStatus.CANCELLED
            )
            if not reopening_blocked and not uncertainty_preserved:
                node.status = status
            node.parent_id = parent_id or node.parent_id
            node.agent = agent or node.agent
            node.summary = summary or node.summary
            node.tool_call_id = tool_call_id or node.tool_call_id
            node.parallel_group_id = parallel_group_id or node.parallel_group_id
            node.updated_at = now
        self._notify()

    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            try:
                listener()
            except Exception:
                logger.debug("Task status listener failed", exc_info=True)


def _session_id(data: Mapping[str, Any]) -> str:
    return _text(
        data.get("child_session_id")
        or data.get("sub_session_id")
        or data.get("session_id")
    )[:_MAX_ID_CHARS]


def _parent_id(data: Mapping[str, Any]) -> str:
    return _text(data.get("parent_session_id") or data.get("parent_id"))[:_MAX_ID_CHARS]


def _agent_name(data: Mapping[str, Any], session_id: str) -> str:
    explicit = _text(data.get("agent") or data.get("agent_name"))
    if explicit:
        return _clean_text(explicit)
    if "_" in session_id:
        return _clean_text(session_id.rsplit("_", 1)[-1])
    return ""


def _terminal_status(event: str, data: Mapping[str, Any]) -> TaskStatus:
    if event == "delegate:agent_cancelled":
        return TaskStatus.CANCELLED
    if event == "delegate:error":
        return TaskStatus.FAILED
    raw_status = _text(data.get("status")).lower()
    fallback = (
        TaskStatus.FAILED if data.get("success") is False else TaskStatus.COMPLETED
    )
    return _status_from_value(raw_status) or fallback


def _status_from_value(value: str) -> TaskStatus | None:
    aliases = {
        "running": TaskStatus.RUNNING,
        "in_progress": TaskStatus.RUNNING,
        "success": TaskStatus.COMPLETED,
        "completed": TaskStatus.COMPLETED,
        "complete": TaskStatus.COMPLETED,
        "failed": TaskStatus.FAILED,
        "error": TaskStatus.FAILED,
        "cancelled": TaskStatus.CANCELLED,
        "canceled": TaskStatus.CANCELLED,
        "incomplete": TaskStatus.INCOMPLETE,
    }
    return aliases.get(value)


def _tool_output(data: Mapping[str, Any]) -> Mapping[str, Any]:
    result: Any = data.get("tool_response", data.get("result", {}))
    if not isinstance(result, Mapping) and hasattr(result, "output"):
        result = result.output
    result_mapping = _as_mapping(result)
    nested = result_mapping.get("output")
    return _as_mapping(nested) or result_mapping


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()[:MAX_TASK_TEXT_CHARS]


def _clean_text(value: str) -> str:
    return " ".join(value.split())[:MAX_TASK_TEXT_CHARS]
