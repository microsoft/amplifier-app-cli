"""Typed, bounded agent-lane state for the compact task board."""

from __future__ import annotations

import re
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum

from prompt_toolkit.utils import get_cwidth

from .runtime_status import RuntimeStatusTracker
from .runtime_values import MAX_DURATION_SECONDS
from .runtime_values import RuntimeStatusSnapshot
from .runtime_values import ToolActivitySnapshot
from .runtime_values import ToolActivityStatus
from .runtime_values import clean_line
from .runtime_values import identifier
from .task_status import TaskNode
from .task_status import TaskStatus
from .task_status import TaskStatusTracker

MAX_AGENT_LANES = 64
MAX_AGENT_CHARS = 64
MAX_LANE_SUMMARY_CHARS = 192

_TEST_COMMAND_RE = re.compile(
    r"(?:^|(?:&&|\|\||;)\s*)"
    r"(?:uv\s+run\s+pytest|python\s+-m\s+pytest|pytest|"
    r"npm\s+(?:run\s+)?test|pnpm\s+test|yarn\s+test|bun\s+test|"
    r"cargo\s+test|go\s+test)(?:\s|$)",
    re.IGNORECASE,
)
_TEST_TOOL_NAMES = frozenset(
    {"pytest", "test", "tests", "test-runner", "test_runner", "testing"}
)

logger = logging.getLogger(__name__)


class AgentTestOutcome(str, Enum):
    NONE = "none"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"

    @property
    def label(self) -> str:
        return {
            AgentTestOutcome.NONE: "",
            AgentTestOutcome.RUNNING: "tests ◐",
            AgentTestOutcome.PASSED: "tests ✔",
            AgentTestOutcome.FAILED: "tests ✘",
        }[self]


@dataclass(frozen=True, slots=True)
class AgentLaneSnapshot:
    """One delegated session rendered as a single compact lane."""

    session_id: str
    parent_session_id: str
    agent: str
    status: TaskStatus
    glyph: str
    summary: str
    elapsed_seconds: float
    cost_usd: Decimal | None
    test_outcome: AgentTestOutcome
    selected: bool
    focused: bool

    def render(self, *, max_columns: int = 96, agent_width: int | None = None) -> str:
        """Render one line without exceeding the terminal-cell budget."""
        max_columns = max(1, int(max_columns))
        width = agent_width if agent_width is not None else get_cwidth(self.agent)
        width = max(1, min(20, int(width)))
        agent = _truncate_cells(self.agent, width)
        padded_agent = _pad_cells(agent, width)
        summary = self.summary or _status_summary(self.status)
        details = [item for item in (self.test_outcome.label,) if item]
        details.extend(
            (_format_elapsed(self.elapsed_seconds), _format_cost(self.cost_usd))
        )
        suffix = " · ".join(details)
        head = f"{self.glyph} {padded_agent} · "
        tail = f" · {suffix}"
        summary_budget = max_columns - get_cwidth(head) - get_cwidth(tail)
        if summary_budget > 0:
            line = head + _truncate_cells(summary, summary_budget) + tail
            if get_cwidth(line) <= max_columns:
                return line

        compact_details = [item for item in (self.test_outcome.label,) if item]
        compact_details.append(_format_cost(self.cost_usd))
        compact_tail = " · ".join(compact_details)
        compact_head = f"{self.glyph} "
        agent_budget = (
            max_columns - get_cwidth(compact_head) - get_cwidth(f" · {compact_tail}")
        )
        compact = (
            compact_head
            + _truncate_cells(self.agent, max(1, agent_budget))
            + f" · {compact_tail}"
        )
        return _truncate_cells(compact, max_columns)

    def render_tree(self, *, max_columns: int = 96) -> str:
        """Render the in-transcript subagent tree body: name · activity · $cost."""
        summary = self.summary or _status_summary(self.status)
        line = f"{self.agent} · {summary} · {_format_cost(self.cost_usd)}"
        return _truncate_cells(line, max(1, int(max_columns)))


@dataclass(frozen=True, slots=True)
class AgentLaneBoardSnapshot:
    """Immutable lane board plus keyboard-navigation state."""

    root_session_id: str
    selected_session_id: str | None
    focused_session_id: str
    focused_parent_session_id: str | None
    lanes: tuple[AgentLaneSnapshot, ...]

    @property
    def selected_lane(self) -> AgentLaneSnapshot | None:
        return next((lane for lane in self.lanes if lane.selected), None)

    def render_lines(self, *, max_columns: int = 96) -> tuple[str, ...]:
        agent_width = min(
            20,
            max((get_cwidth(lane.agent) for lane in self.lanes), default=1),
        )
        return tuple(
            lane.render(max_columns=max_columns, agent_width=agent_width)
            for lane in self.lanes
        )


class AgentLaneViewModel:
    """Adapt task/runtime trackers into navigable immutable lane snapshots."""

    def __init__(
        self,
        tasks: TaskStatusTracker,
        runtime: RuntimeStatusTracker | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        max_lanes: int = MAX_AGENT_LANES,
    ) -> None:
        self._tasks = tasks
        self._runtime = runtime
        self._clock = clock or (lambda: datetime.now(UTC))
        self._max_lanes = max(1, min(MAX_AGENT_LANES, int(max_lanes)))
        self._selected_session_id: str | None = None
        self._focused_session_id = tasks.root_session_id
        self._listeners: list[Callable[[], None]] = []
        self._remove_task_listener = tasks.add_listener(self._source_changed)
        self._remove_runtime_listener = (
            runtime.add_listener(self._source_changed) if runtime is not None else None
        )

    @property
    def selected_session_id(self) -> str | None:
        return self._selected_session_id

    @property
    def focused_session_id(self) -> str:
        return self._focused_session_id

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    def close(self) -> None:
        """Detach source listeners when the interactive session ends."""
        self._remove_task_listener()
        if self._remove_runtime_listener is not None:
            self._remove_runtime_listener()

    def snapshot(self) -> AgentLaneBoardSnapshot:
        nodes = self._visible_nodes(self._tasks.nodes())
        visible_ids = {node.session_id for node in nodes}
        if self._selected_session_id not in visible_ids:
            self._selected_session_id = nodes[0].session_id if nodes else None

        all_nodes = {node.session_id: node for node in self._tasks.nodes()}
        if (
            self._focused_session_id != self._tasks.root_session_id
            and self._focused_session_id not in all_nodes
        ):
            self._focused_session_id = self._tasks.root_session_id
        runtime = self._runtime.snapshot() if self._runtime is not None else None
        now = _as_aware(self._clock())
        lanes = tuple(self._lane(node, runtime, now) for node in nodes)
        focused_node = all_nodes.get(self._focused_session_id)
        parent = focused_node.parent_id if focused_node is not None else None
        return AgentLaneBoardSnapshot(
            root_session_id=self._tasks.root_session_id,
            selected_session_id=self._selected_session_id,
            focused_session_id=self._focused_session_id,
            focused_parent_session_id=parent,
            lanes=lanes,
        )

    def select_next(self) -> AgentLaneBoardSnapshot:
        return self._move_selection(1)

    def select_previous(self) -> AgentLaneBoardSnapshot:
        return self._move_selection(-1)

    def select(self, session_id: str) -> AgentLaneBoardSnapshot:
        candidate = identifier(session_id, "")
        if candidate in {node.session_id for node in self._tasks.nodes()}:
            self._selected_session_id = candidate
            self._notify()
        return self.snapshot()

    def focus_selected(self) -> str | None:
        """Apply the Enter transition and return the transcript session id."""
        selected = self.snapshot().selected_session_id
        if selected is None:
            return None
        self._focused_session_id = selected
        self._notify()
        return selected

    def focus_parent(self) -> str:
        """Apply the Esc transition and return the parent transcript session id."""
        nodes = {node.session_id: node for node in self._tasks.nodes()}
        focused = nodes.get(self._focused_session_id)
        target = (
            focused.parent_id if focused is not None else self._tasks.root_session_id
        )
        if target != self._tasks.root_session_id and target not in nodes:
            target = self._tasks.root_session_id
        self._focused_session_id = target
        if target != self._tasks.root_session_id:
            self._selected_session_id = target
        self._notify()
        return target

    def _move_selection(self, offset: int) -> AgentLaneBoardSnapshot:
        snapshot = self.snapshot()
        session_ids = [lane.session_id for lane in snapshot.lanes]
        if not session_ids:
            return snapshot
        try:
            current = session_ids.index(self._selected_session_id or "")
        except ValueError:
            current = 0
        self._selected_session_id = session_ids[(current + offset) % len(session_ids)]
        self._notify()
        return self.snapshot()

    def _visible_nodes(self, nodes: Sequence[TaskNode]) -> tuple[TaskNode, ...]:
        if len(nodes) <= self._max_lanes:
            return tuple(nodes)
        selected = sorted(
            nodes,
            key=lambda node: (
                node.session_id == self._selected_session_id,
                node.status == TaskStatus.RUNNING,
                _as_aware(node.updated_at),
                node.order,
            ),
            reverse=True,
        )[: self._max_lanes]
        return tuple(sorted(selected, key=lambda node: node.order))

    def _lane(
        self,
        node: TaskNode,
        runtime: RuntimeStatusSnapshot | None,
        now: datetime,
    ) -> AgentLaneSnapshot:
        tools = (
            tuple(tool for tool in runtime.tools if tool.session_id == node.session_id)
            if runtime is not None
            else ()
        )
        running = [tool for tool in tools if not tool.terminal]
        active_tool = max(running, key=lambda tool: tool.started_at, default=None)
        summary = _lane_summary(node, active_tool)
        test_outcome = _test_outcome(tools)
        costs = (
            {item.session_id: item.usage.cost_usd for item in runtime.session_usage}
            if runtime is not None
            else {}
        )
        selected = node.session_id == self._selected_session_id
        return AgentLaneSnapshot(
            session_id=identifier(node.session_id, "agent"),
            parent_session_id=identifier(node.parent_id, self._tasks.root_session_id),
            agent=clean_line(node.agent, MAX_AGENT_CHARS) or "agent",
            status=node.status,
            glyph=_status_glyph(node.status, active=active_tool is not None),
            summary=summary,
            elapsed_seconds=_elapsed(node, now),
            cost_usd=costs.get(node.session_id),
            test_outcome=test_outcome,
            selected=selected,
            focused=node.session_id == self._focused_session_id,
        )

    def _source_changed(self) -> None:
        self._notify()

    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            try:
                listener()
            except Exception:
                logger.debug("Agent lane listener failed", exc_info=True)


def _lane_summary(node: TaskNode, active_tool: ToolActivitySnapshot | None) -> str:
    if active_tool is not None:
        summary = active_tool.summary or active_tool.command or active_tool.tool_name
    elif node.status == TaskStatus.RUNNING:
        summary = node.summary or "working"
    else:
        summary = _status_summary(node.status)
    return clean_line(summary, MAX_LANE_SUMMARY_CHARS) or "working"


def _status_summary(status: TaskStatus) -> str:
    return {
        TaskStatus.RUNNING: "working",
        TaskStatus.COMPLETED: "done",
        TaskStatus.FAILED: "failed",
        TaskStatus.CANCELLED: "cancelled",
        TaskStatus.INCOMPLETE: "incomplete",
    }[status]


def _status_glyph(status: TaskStatus, *, active: bool) -> str:
    """Spec glyphs: ◐ running a tool (teal), ■ working (fg), ✔ done."""
    if status == TaskStatus.RUNNING:
        return "◐" if active else "■"
    return {
        TaskStatus.COMPLETED: "✔",
        TaskStatus.FAILED: "✘",
        TaskStatus.CANCELLED: "□",
        TaskStatus.INCOMPLETE: "□",
    }[status]


def _test_outcome(tools: Sequence[ToolActivitySnapshot]) -> AgentTestOutcome:
    tests = [tool for tool in tools if _is_test_tool(tool)]
    if not tests:
        return AgentTestOutcome.NONE
    latest = max(tests, key=lambda tool: tool.started_at)
    return {
        ToolActivityStatus.RUNNING: AgentTestOutcome.RUNNING,
        ToolActivityStatus.SUCCEEDED: AgentTestOutcome.PASSED,
        ToolActivityStatus.FAILED: AgentTestOutcome.FAILED,
    }[latest.status]


def _is_test_tool(tool: ToolActivitySnapshot) -> bool:
    name = tool.tool_name.lower().replace(" ", "_")
    if name in _TEST_TOOL_NAMES:
        return True
    command = " ".join(tool.command.split())
    return bool(_TEST_COMMAND_RE.search(command))


def _elapsed(node: TaskNode, now: datetime) -> float:
    end = now if node.status == TaskStatus.RUNNING else _as_aware(node.updated_at)
    value = (end - _as_aware(node.started_at)).total_seconds()
    return max(0.0, min(MAX_DURATION_SECONDS, value))


def _as_aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _format_elapsed(seconds: float) -> str:
    seconds = max(0, round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes = max(1, round(seconds / 60))
    if minutes < 60:
        return f"{minutes}m"
    hours, remainder = divmod(minutes, 60)
    return f"{hours}h" if remainder == 0 else f"{hours}h {remainder}m"


def _format_cost(cost: Decimal | None) -> str:
    return "$—" if cost is None else f"${cost:.2f}"


def _pad_cells(value: str, width: int) -> str:
    return value + " " * max(0, width - get_cwidth(value))


def _truncate_cells(value: str, width: int) -> str:
    width = max(0, int(width))
    if get_cwidth(value) <= width:
        return value
    suffix = "…" if width > 1 else ""
    result = ""
    for char in value:
        if get_cwidth(result + char + suffix) > width:
            break
        result += char
    return result.rstrip() + suffix


__all__ = [
    "AgentLaneBoardSnapshot",
    "AgentLaneSnapshot",
    "AgentLaneViewModel",
    "AgentTestOutcome",
    "MAX_AGENT_LANES",
]
