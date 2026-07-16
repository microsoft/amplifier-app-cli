"""Agent-lane selection and focused child transcript behavior."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING
from typing import Any
from typing import Protocol

from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.formatted_text.utils import fragment_list_to_text
from prompt_toolkit.layout.dimension import Dimension

from amplifier_app_cli.session_store import sanitize_message

from .layered_repl_style import TOKENS
from .notices import NoticeKind
from .task_status import TaskStatus
from .transcript_blocks import AnswerBlock
from .transcript_blocks import NarrationBlock
from .transcript_blocks import UserBlock

if TYPE_CHECKING:
    from prompt_toolkit.application import Application

    from amplifier_app_cli.session_store import SessionStore

    from .agent_lanes import AgentLaneViewModel
    from .notices import TransientNoticeState
    from .task_status import TaskCounts
    from .task_status import TaskStatusTracker
    from .ui_events import UiEvent

    class _LayeredReplAgentOwner(Protocol):
        application: Application[Any]
        _agent_lanes: AgentLaneViewModel | None
        _committed_plan_signature: tuple[tuple[str, str], ...] | None
        _focused_transcript_revisions: dict[str, tuple[int, int]]
        _focused_transcript_signatures: dict[str, tuple[str, ...]]
        _focused_transcript_task: asyncio.Task[None] | None
        _last_task_counts: TaskCounts | None
        _notices: TransientNoticeState
        _owner_loop: asyncio.AbstractEventLoop | None
        _session_id: str | None
        _session_store: SessionStore
        _task_tracker: TaskStatusTracker | None
        _tasks_visible: bool

        def _follow_focused_transcript(self, session_id: str) -> Any: ...

        def _refresh_focused_transcript(self) -> None: ...

        def _start_focused_transcript_follow(self, session_id: str) -> None: ...

        def _stop_focused_transcript_follow(self) -> None: ...

        def _sync_focused_child_transcript(
            self, session_id: str | None = None
        ) -> int: ...

        def _task_line_budget(self) -> int: ...

        def _task_pane_text(self) -> FormattedText: ...

        def _emit_ui_event(self, event: UiEvent) -> None: ...

        def _runtime_state_changed(self) -> None: ...

        def _terminal_size(self) -> tuple[int, int]: ...

        def close_task_pane(self) -> None: ...

        def commit_plan_state(self, lifecycle: str) -> bool: ...


class LayeredReplAgentMixin:
    """Expose agent lanes and follow the selected child transcript."""

    @property
    def tasks_visible(self: _LayeredReplAgentOwner) -> bool:
        return self._tasks_visible

    def toggle_task_pane(self: _LayeredReplAgentOwner) -> None:
        self._tasks_visible = not self._tasks_visible
        self.application.invalidate()

    def close_task_pane(self: _LayeredReplAgentOwner) -> None:
        if self._tasks_visible:
            self._tasks_visible = False
            self.application.invalidate()

    def select_next_lane(self: _LayeredReplAgentOwner, offset: int) -> None:
        if self._agent_lanes is None:
            return
        if offset < 0:
            self._agent_lanes.select_previous()
        else:
            self._agent_lanes.select_next()

    def focus_selected_lane(self: _LayeredReplAgentOwner) -> None:
        if self._agent_lanes is None:
            return
        lane = self._agent_lanes.snapshot().selected_lane
        session_id = self._agent_lanes.focus_selected()
        if session_id:
            focused = (
                lane if lane is not None and lane.session_id == session_id else None
            )
            name = focused.agent if focused is not None else session_id[:8]
            parent = focused.parent_session_id[:8] if focused is not None else "parent"
            self._notices.show(f"focused: {name} · esc back")
            self._emit_ui_event(
                NarrationBlock(
                    f"focused: {name} · subagent of {parent} · own context window"
                    " · results report back to parent · esc back"
                )
            )
            self._sync_focused_child_transcript(session_id)
            self._start_focused_transcript_follow(session_id)
            self._runtime_state_changed()

    def _sync_focused_child_transcript(
        self: _LayeredReplAgentOwner, session_id: str | None = None
    ) -> int:
        """Commit newly persisted focused-child messages to the transcript."""
        if self._agent_lanes is None:
            return 0
        focused = session_id or self._agent_lanes.focused_session_id
        if not focused or focused == self._session_id:
            return 0
        transcript_path = self._session_store.base_dir / focused / "transcript.jsonl"
        try:
            stat = transcript_path.stat()
            revision = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            revision = None
        if (
            revision is not None
            and self._focused_transcript_revisions.get(focused) == revision
        ):
            return 0
        try:
            messages, _ = self._session_store.load(focused)
        except (FileNotFoundError, OSError, ValueError):
            return 0
        displayable: list[tuple[str, dict[str, Any]]] = []
        for raw in messages:
            message = sanitize_message(raw)
            role = str(message.get("role") or "message")
            if role not in {"user", "assistant"}:
                continue
            text = _displayable_message_text(message)
            if not text:
                continue
            signature = json.dumps(
                {"role": role, "content": message.get("content")},
                ensure_ascii=True,
                sort_keys=True,
                default=str,
            )
            displayable.append((signature, {"role": role, "text": text}))

        signatures = tuple(item[0] for item in displayable)
        previous = self._focused_transcript_signatures.get(focused, ())
        common = 0
        for before, current in zip(previous, signatures):
            if before != current:
                break
            common += 1
        if common < len(previous):
            self._emit_ui_event(
                NarrationBlock(f"Agent {focused[:8]} transcript was revised")
            )
        committed = 0
        for _, message in displayable[common:]:
            if message["role"] == "user":
                self._emit_ui_event(UserBlock(message["text"], mode="agent"))
            else:
                self._emit_ui_event(
                    AnswerBlock(message["text"], label=f"Agent {focused[:8]}")
                )
            committed += 1
        self._focused_transcript_signatures[focused] = signatures
        if revision is not None:
            self._focused_transcript_revisions[focused] = revision
        return committed

    def _start_focused_transcript_follow(
        self: _LayeredReplAgentOwner, session_id: str
    ) -> None:
        self._stop_focused_transcript_follow()
        owner_loop = self._owner_loop
        if owner_loop is None or owner_loop.is_closed():
            return
        self._focused_transcript_task = owner_loop.create_task(
            self._follow_focused_transcript(session_id)
        )

    def _stop_focused_transcript_follow(self: _LayeredReplAgentOwner) -> None:
        task = self._focused_transcript_task
        self._focused_transcript_task = None
        if task is not None and not task.done():
            task.cancel()

    async def _follow_focused_transcript(
        self: _LayeredReplAgentOwner, session_id: str
    ) -> None:
        try:
            while (
                self._agent_lanes is not None
                and self._agent_lanes.focused_session_id == session_id
                and not self.application.is_done
            ):
                await asyncio.sleep(0.25)
                self._sync_focused_child_transcript(session_id)
        except asyncio.CancelledError:
            return

    def _refresh_focused_transcript(self: _LayeredReplAgentOwner) -> None:
        if (
            self._agent_lanes is not None
            and self._agent_lanes.focused_session_id != self._session_id
        ):
            self._sync_focused_child_transcript()

    def leave_agent_focus(self: _LayeredReplAgentOwner) -> None:
        if self._agent_lanes is None:
            self.close_task_pane()
            return
        if self._agent_lanes.focused_session_id == self._session_id:
            self.close_task_pane()
            return
        self._stop_focused_transcript_follow()
        parent = self._agent_lanes.focus_parent()
        self._notices.show(
            "focused parent" if parent == self._session_id else f"focused {parent[:8]}"
        )
        if parent != self._session_id:
            self._sync_focused_child_transcript(parent)
            self._start_focused_transcript_follow(parent)
        else:
            self._emit_ui_event(NarrationBlock("Returned to parent transcript"))
        self._runtime_state_changed()

    def _task_pane_height(self: _LayeredReplAgentOwner) -> Dimension:
        line_count = fragment_list_to_text(self._task_pane_text()).count("\n") + 1
        return Dimension.exact(min(self._task_line_budget(), max(4, line_count)))

    def _task_pane_text(self: _LayeredReplAgentOwner) -> FormattedText:
        if self._agent_lanes is None:
            return FormattedText([("class:tasks.muted", "  No delegated agents")])
        snapshot = self._agent_lanes.snapshot()
        lines = snapshot.render_lines(max_columns=self._terminal_size()[1] - 2)
        fragments: list[tuple[str, str]] = [
            ("class:tasks.title", " Agent lanes"),
            (f"fg:{TOKENS['dimmer']}", " · ↑↓ select · enter focus · esc close\n"),
        ]
        if not lines:
            fragments.append(("class:tasks.muted", "  No delegated agents"))
        else:
            for index, (lane, line) in enumerate(
                zip(snapshot.lanes, lines, strict=True)
            ):
                glyph, _, body = line.partition(" ")
                glyph_style = {
                    "◐": "class:tasks.running",
                    "■": "class:tasks",
                    "✔": "class:tasks.completed",
                    "✘": "class:tasks.failed",
                }.get(glyph, "class:tasks.muted")
                body_style = (
                    "class:tasks"
                    if lane.status == TaskStatus.RUNNING
                    else "class:tasks.muted"
                )
                if lane.selected:
                    glyph_style = f"{glyph_style} bg:{TOKENS['bg_tab']}"
                    body_style = "class:selected"
                ending = "\n" if index < len(lines) - 1 else ""
                fragments.append((glyph_style, f" {glyph} "))
                fragments.append((body_style, f"{body}{ending}"))
        return FormattedText(fragments)

    def _task_state_changed(self: _LayeredReplAgentOwner) -> None:
        self._refresh_focused_transcript()
        if self._task_tracker is not None:
            counts = self._task_tracker.counts()
            previous = self._last_task_counts
            if previous is not None:
                completed = max(0, counts.completed - previous.completed)
                failed = max(0, counts.failed - previous.failed)
                if failed:
                    self._notices.show(f"agents {failed} failed", kind=NoticeKind.ERROR)
                elif completed:
                    self._notices.show(
                        f"agents {completed} done", kind=NoticeKind.SUCCESS
                    )
            self._last_task_counts = counts
            plan = self._task_tracker.plan_snapshot()
            signature = tuple((item.content, item.status) for item in plan.items)
            if plan.items and all(item.status == "completed" for item in plan.items):
                if signature != self._committed_plan_signature:
                    self.commit_plan_state("completed")
                    self._committed_plan_signature = signature
            elif plan.items:
                self._committed_plan_signature = None
        application = getattr(self, "application", None)
        if application is not None:
            application.invalidate()

    def _task_line_budget(self: _LayeredReplAgentOwner) -> int:
        return min(16, max(4, self._terminal_size()[0] - 6))


def _displayable_message_text(message: dict[str, Any]) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return str(content).strip()
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and block.get("text"):
            parts.append(str(block["text"]))
        elif block.get("type") == "image":
            parts.append("[Image attachment]")
    return "\n".join(parts).strip()


__all__ = ["LayeredReplAgentMixin"]
