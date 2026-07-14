"""Transient surface rendering for the layered REPL."""

from __future__ import annotations

import logging
from collections.abc import Callable
from math import ceil
from typing import TYPE_CHECKING
from typing import Any
from typing import Protocol

from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.formatted_text.utils import fragment_list_len
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.utils import get_cwidth

from .layered_repl_status import format_tokens
from .notices import NoticeKind
from .repl import format_elapsed
from .repl import summarize_cell_text
from .transcript_blocks import PlanBlock
from .transcript_blocks import PlanItem as RenderPlanItem
from .transcript_blocks import PlanItemStatus
from .transcript_blocks import telemetry_from_usage
from .transcript_blocks import tool_block_from_activity

if TYPE_CHECKING:
    from prompt_toolkit.application import Application
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.layout.containers import Window

    from .agent_lanes import AgentLaneViewModel
    from .interaction_state import SteeringQueue
    from .layered_transcript import LayeredTranscriptView
    from .notices import TransientNoticeState
    from .runtime_status import RuntimeStatusTracker
    from .stream_status import StreamStatusTracker
    from .task_status import TaskStatusTracker
    from .ui_events import UiEvent
    from .ui_events import UiEventDispatcher

    class _LayeredReplSurfaceOwner(Protocol):
        input_buffer: Buffer
        application: Application[Any]
        transcript_window: Window
        _runtime_status: RuntimeStatusTracker | None
        _bundle_name: str
        _session_id: str | None
        _get_active_mode: Callable[[], str | None] | None
        _get_queued_count: Callable[[], int] | None
        _get_task_title: Callable[[], str | None] | None
        _task_tracker: TaskStatusTracker | None
        _agent_lanes: AgentLaneViewModel | None
        _notices: TransientNoticeState
        _committed_plan_signature: tuple[tuple[str, str], ...] | None
        _committed_plan_lifecycle: tuple[tuple[tuple[str, str], ...], str] | None
        _steering_queue: SteeringQueue | None
        _rendered_terminal_tools: set[tuple[str, str]]
        _expanded_terminal_tools: set[tuple[str, str]]
        _ui_events: UiEventDispatcher
        _stream_status: StreamStatusTracker | None
        _tasks_visible: bool
        _transcript_view: LayeredTranscriptView

        def _active_mode(self) -> str | None: ...

        def _is_running(self) -> bool: ...

        def _queued_count(self) -> int: ...

        def _approval_visible(self) -> bool: ...

        def _terminal_size(self) -> tuple[int, int]: ...

        def _prompt_width(self) -> Dimension: ...

        def _plan_visible(self) -> bool: ...

        def _plan_height(self) -> Dimension: ...

        def _steering_visible(self) -> bool: ...

        def _preview_visible(self) -> bool: ...

        def _preview_height(self) -> Dimension: ...

        def _running_tools_visible(self) -> bool: ...

        def _running_tools_height(self) -> Dimension: ...

        def _work_visible(self) -> bool: ...

        def _working_height(self) -> Dimension: ...

        def _notice_visible(self) -> bool: ...

        def _palette_visible(self) -> bool: ...

        def _palette_height(self) -> Dimension: ...

        def _rewind_visible(self) -> bool: ...

        def _evidence_visible(self) -> bool: ...

        def _task_pane_height(self) -> Dimension: ...

        def _task_pane_text(self) -> FormattedText: ...

        def _task_line_budget(self) -> int: ...

        def _refresh_focused_transcript(self) -> None: ...

        def commit_plan_state(self, lifecycle: str) -> bool: ...

        def _emit_ui_event(self, event: UiEvent) -> None: ...

        def _running_tools(self) -> tuple[Any, ...]: ...

        def _prompt_text(self) -> FormattedText: ...

        def _transcript_page_rows(self) -> int: ...


logger = logging.getLogger(__name__)


class LayeredReplSurfaceMixin:
    """Render live state without retaining immutable transcript output."""

    def _input_height(self: _LayeredReplSurfaceOwner) -> Dimension:
        rows, columns = self._terminal_size()
        input_width = max(1, columns - (self._prompt_width().preferred or 1))
        document = self.input_buffer.document
        visual_rows = 0
        for index, logical_line in enumerate(document.lines):
            cell_width = get_cwidth(logical_line.expandtabs(4))
            if index == document.cursor_position_row:
                before_cursor = logical_line[: document.cursor_position_col]
                cursor_width = get_cwidth(before_cursor.expandtabs(4)) + 1
                cell_width = max(cell_width, cursor_width)
            visual_rows += max(1, ceil(cell_width / input_width))

        # Keep one transcript row in addition to the editor and footer.
        reserved_rows = 3
        if self._plan_visible():
            reserved_rows += self._plan_height().preferred or 0
        if self._steering_visible():
            reserved_rows += 1
        if self._preview_visible():
            reserved_rows += self._preview_height().preferred or 0
        if self._running_tools_visible():
            reserved_rows += self._running_tools_height().preferred or 0
        if self._work_visible():
            reserved_rows += self._working_height().preferred or 1
        if self._notice_visible():
            reserved_rows += 1
        if self._palette_visible():
            reserved_rows += self._palette_height().preferred or 0
        if self._rewind_visible():
            reserved_rows += 1
        if self._evidence_visible():
            reserved_rows += 1
        if self._tasks_visible:
            reserved_rows += self._task_pane_height().preferred or 0
        height_cap = min(8, max(1, rows - reserved_rows))
        return Dimension.exact(min(height_cap, max(1, visual_rows)))

    def _terminal_size(self: _LayeredReplSurfaceOwner) -> tuple[int, int]:
        application = getattr(self, "application", None)
        output = getattr(application, "output", None)
        if output is not None:
            try:
                size = output.get_size()
                return max(1, size.rows), max(1, size.columns)
            except Exception:
                logger.debug("Could not read terminal size", exc_info=True)
        return 24, 80

    def commit_plan_state(self: _LayeredReplSurfaceOwner, lifecycle: str) -> bool:
        """Commit terminal plan state before its transient widget disappears."""
        if self._task_tracker is None:
            return False
        plan = self._task_tracker.plan_snapshot()
        if not plan.items:
            return False
        normalized = lifecycle.strip().lower()
        if normalized not in {"completed", "interrupted", "failed", "incomplete"}:
            raise ValueError(f"unsupported plan lifecycle: {lifecycle}")
        if normalized != "completed" and all(
            item.status == "completed" for item in plan.items
        ):
            return False
        signature = tuple((item.content, item.status) for item in plan.items)
        committed = (signature, normalized)
        if committed == self._committed_plan_lifecycle:
            return False
        task_title = (
            self._get_task_title() if self._get_task_title is not None else None
        )
        lifecycle_title = {
            "completed": "Plan complete",
            "interrupted": "Plan interrupted",
            "failed": "Plan failed",
            "incomplete": "Plan incomplete",
        }[normalized]
        title = (
            f"{task_title} · {normalized}"
            if task_title and normalized != "completed"
            else task_title or lifecycle_title
        )
        telemetry = (
            telemetry_from_usage(self._runtime_status.telemetry_snapshot().turn)
            if self._runtime_status is not None
            else None
        )
        items = tuple(
            RenderPlanItem(
                item.content,
                {
                    "completed": PlanItemStatus.COMPLETED,
                    "in_progress": PlanItemStatus.ACTIVE,
                }.get(item.status, PlanItemStatus.PENDING),
            )
            for item in plan.items
        )
        self._emit_ui_event(PlanBlock(title, items, telemetry))
        self._committed_plan_lifecycle = committed
        if normalized == "completed":
            self._committed_plan_signature = signature
        return True

    def _plan_visible(self: _LayeredReplSurfaceOwner) -> bool:
        if self._task_tracker is None:
            return False
        plan = self._task_tracker.plan_snapshot()
        signature = tuple((item.content, item.status) for item in plan.items)
        return bool(
            plan.items
            and not (
                all(item.status == "completed" for item in plan.items)
                and signature == self._committed_plan_signature
            )
        )

    def _plan_height(self: _LayeredReplSurfaceOwner) -> Dimension:
        count = (
            len(self._task_tracker.plan_snapshot().items)
            if self._task_tracker is not None
            else 0
        )
        return Dimension.exact(min(8, max(1, count + 1)))

    def _plan_text(self: _LayeredReplSurfaceOwner) -> FormattedText:
        if self._task_tracker is None:
            return FormattedText()
        snapshot = self._task_tracker.plan_snapshot()
        title = self._get_task_title() if self._get_task_title else None
        title = title or "Current plan"
        fragments: list[tuple[str, str]] = [
            ("class:plan.header", f"· {title}"),
        ]
        telemetry = (
            self._runtime_status.telemetry_snapshot().turn
            if self._runtime_status is not None
            else None
        )
        if telemetry is not None and telemetry.request_count:
            suffix = (
                f" ({format_elapsed(telemetry.duration_seconds)}"
                f" · ↓ {format_tokens(telemetry.total_tokens)} tok)"
            )
            fragments.append(("class:plan.pending", suffix))
        fragments.append(("", "\n"))
        for index, item in enumerate(snapshot.items):
            marker, style = {
                "completed": ("✔", "class:plan.done"),
                "in_progress": ("■", "class:plan.active"),
            }.get(item.status, ("□", "class:plan.pending"))
            ending = "\n" if index < len(snapshot.items) - 1 else ""
            fragments.append((style, f"  {marker} {item.display_text}{ending}"))
        return FormattedText(fragments)

    def _steering_visible(self: _LayeredReplSurfaceOwner) -> bool:
        return bool(self._steering_queue and self._steering_queue.pending)

    def _steering_text(self: _LayeredReplSurfaceOwner) -> FormattedText:
        if not self._steering_queue or not self._steering_queue.pending:
            return FormattedText()
        steer = self._steering_queue.pending[0]
        summary = summarize_cell_text(
            steer.display_text or steer.text,
            max_cells=max(1, self._terminal_size()[1] - 58),
        )
        return FormattedText(
            [
                (
                    "class:steering",
                    f'  ↳ steer queued: "{summary}" · applies at next step boundary',
                )
            ]
        )

    def _running_tools(self: _LayeredReplSurfaceOwner):
        if self._runtime_status is None:
            return ()
        tools = tuple(
            tool for tool in self._runtime_status.tool_snapshot() if not tool.terminal
        )
        focused = (
            self._agent_lanes.focused_session_id
            if self._agent_lanes is not None
            else self._session_id
        )
        tools = tuple(tool for tool in tools if tool.session_id == focused)
        return tools[-4:]

    def _running_tools_visible(self: _LayeredReplSurfaceOwner) -> bool:
        return bool(self._running_tools())

    def _running_tools_height(self: _LayeredReplSurfaceOwner) -> Dimension:
        lines = sum(1 + bool(tool.command) for tool in self._running_tools())
        return Dimension.exact(min(8, max(1, lines)))

    def _running_tools_text(self: _LayeredReplSurfaceOwner) -> FormattedText:
        fragments: list[tuple[str, str]] = []
        tools = self._running_tools()
        for index, tool in enumerate(tools):
            summary = tool.summary or f"Running {tool.tool_name}"
            fragments.append(("class:tools", f"  ● {summary}\n"))
            if tool.command:
                ending = "\n" if index < len(tools) - 1 else ""
                fragments.append(("class:tools", f"    └ {tool.command}{ending}"))
        return FormattedText(fragments)

    def _runtime_state_changed(self: _LayeredReplSurfaceOwner) -> None:
        if self._runtime_status is None:
            return
        self._refresh_focused_transcript()
        focused = (
            self._agent_lanes.focused_session_id
            if self._agent_lanes is not None
            else self._session_id
        )
        for tool in self._runtime_status.tool_snapshot():
            key = (tool.session_id, tool.tool_call_id)
            if not tool.terminal or key in self._rendered_terminal_tools:
                continue
            if tool.session_id != focused:
                continue
            self._emit_ui_event(tool_block_from_activity(tool))
            self._rendered_terminal_tools.add(key)
            if tool.status.value == "failed":
                self._notices.show(f"{tool.tool_name} failed", kind=NoticeKind.ERROR)
        self.application.invalidate()

    def expand_latest_tool(self: _LayeredReplSurfaceOwner) -> None:
        tool = None
        if self._runtime_status is not None:
            tool = next(
                (
                    item
                    for item in reversed(self._runtime_status.tool_snapshot())
                    if (
                        item.terminal
                        and item.session_id
                        == (
                            self._agent_lanes.focused_session_id
                            if self._agent_lanes is not None
                            else self._session_id
                        )
                        and item.result is not None
                        and (item.session_id, item.tool_call_id)
                        not in self._expanded_terminal_tools
                    )
                ),
                None,
            )
        if tool is not None:
            self._expanded_terminal_tools.add((tool.session_id, tool.tool_call_id))
            self._emit_ui_event(tool_block_from_activity(tool, expanded=True))
            self._notices.show(f"expanded {tool.tool_name} output")
            return
        if self._ui_events.expand_latest_debug():
            self._notices.show("expanded internal output")
            return
        self._notices.show("no tool output to expand")

    def _notice_visible(self: _LayeredReplSurfaceOwner) -> bool:
        return self._notices.current() is not None

    def _notice_text(self: _LayeredReplSurfaceOwner) -> FormattedText:
        notice = self._notices.current()
        if notice is None:
            return FormattedText()
        text = summarize_cell_text(
            notice.text, max_cells=max(1, self._terminal_size()[1] - 2)
        )
        padding = max(0, self._terminal_size()[1] - get_cwidth(text) - 1)
        return FormattedText(
            [(f"class:notice.{notice.kind.value}", " " * padding + text)]
        )

    def _prompt_text(self: _LayeredReplSurfaceOwner) -> FormattedText:
        active_mode = self._active_mode()
        mode_style = (
            f"class:mode.{active_mode}"
            if active_mode in {"chat", "plan", "brainstorm", "build", "auto", "bypass"}
            else "class:mode.chat"
        )
        columns = self._terminal_size()[1]
        max_prompt = max(5, columns - 8)
        if columns < 40:
            if active_mode:
                mode = summarize_cell_text(active_mode, max_cells=max_prompt - 4)
                return FormattedText(
                    [
                        ("class:prompt", "❯ "),
                        (mode_style, f"[{mode}] "),
                    ]
                )
            return FormattedText([("class:prompt", "❯ ")])
        if active_mode:
            mode = summarize_cell_text(active_mode, max_cells=max_prompt - 4)
            return FormattedText(
                [
                    ("class:prompt", "❯ "),
                    (mode_style, f"[{mode}] "),
                ]
            )
        return FormattedText([("class:prompt", "❯ ")])

    def _active_mode(self: _LayeredReplSurfaceOwner) -> str | None:
        return self._get_active_mode() if self._get_active_mode else None

    def _queued_count(self: _LayeredReplSurfaceOwner) -> int:
        if not self._get_queued_count:
            return 0
        return max(0, int(self._get_queued_count()))

    def _prompt_width(self: _LayeredReplSurfaceOwner) -> Dimension:
        width = fragment_list_len(self._prompt_text())
        return Dimension.exact(min(width, max(5, self._terminal_size()[1] - 8)))

    def _preview_visible(self: _LayeredReplSurfaceOwner) -> bool:
        return (
            self._stream_status is not None and self._stream_status.preview is not None
        )

    def _preview_height(self: _LayeredReplSurfaceOwner) -> Dimension:
        line_count = self._transcript_view.preview_line_count()
        return Dimension.exact(min(8, max(1, line_count)))

    def _stream_preview_text(self: _LayeredReplSurfaceOwner) -> FormattedText:
        return self._transcript_view.preview_formatted_text()

    def _stream_state_changed(self: _LayeredReplSurfaceOwner) -> None:
        self._transcript_view.refresh_stream()

    def scroll_transcript_page(self: _LayeredReplSurfaceOwner, direction: int) -> None:
        rows = self._transcript_page_rows()
        render_info = getattr(self.transcript_window, "render_info", None)
        top = getattr(render_info, "vertical_scroll", None)
        height = getattr(render_info, "window_height", None)
        if isinstance(top, int) and isinstance(height, int) and height > 0:
            local_target = top - rows if direction < 0 else top + height - 1 + rows
            self._transcript_view.scroll_to_row(
                self._transcript_view.window_start + local_target
            )
            return
        self._transcript_view.scroll_page(direction, rows)


__all__ = ["LayeredReplSurfaceMixin"]
