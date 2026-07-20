"""Persistent status and live working-state rendering."""

from __future__ import annotations

import re
from collections.abc import Callable
from decimal import Decimal
from typing import TYPE_CHECKING
from typing import Any
from typing import Protocol

from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.utils import get_cwidth

from .footer import format_bottom_toolbar_text
from .layered_repl_style import TOKENS
from .repl import format_elapsed
from .repl import summarize_cell_text
from .task_status import TaskStatus

if TYPE_CHECKING:
    from .agent_lanes import AgentLaneViewModel
    from .clipboard_availability import ClipboardImageAvailabilityDetector
    from .interaction_state import NeedsYouQueue
    from .interaction_state import TrustState
    from .outcome_ledger import OutcomeLedger
    from .runtime_status import RuntimeStatusTracker
    from .stream_status import StreamStatusTracker
    from .task_status import TaskStatusTracker

    class _LayeredReplStatusOwner(Protocol):
        _agent_lanes: AgentLaneViewModel | None
        _bundle_name: str
        _clipboard_detector: ClipboardImageAvailabilityDetector
        _get_is_running: Callable[[], bool] | None
        _needs_you: NeedsYouQueue | None
        _outcome_ledger: OutcomeLedger | None
        _running_started_at: float | None
        _runtime_status: RuntimeStatusTracker | None
        _session_id: str | None
        _stream_status: StreamStatusTracker | None
        _task_tracker: TaskStatusTracker | None
        _trust_state: TrustState | None

        def _active_mode(self) -> str | None: ...

        def _approval_visible(self) -> bool: ...

        def capability_hint_overrides(self) -> dict[str, str] | None: ...

        def _clock(self) -> float: ...

        def _is_running(self) -> bool: ...

        def _live_agent_lanes(self) -> tuple[tuple[Any, ...], int]: ...

        def _live_tree_prefixes(self) -> dict[str, str]: ...

        def _palette_visible(self) -> bool: ...

        def _queued_count(self) -> int: ...

        def _terminal_size(self) -> tuple[int, int]: ...

        def _working_stage(self, lanes: tuple[Any, ...]) -> str: ...


_MAX_LIVE_AGENT_ROWS = 4


class LayeredReplStatusMixin:
    """Render footer telemetry and a bounded live task/agent tree."""

    def _status_text(self: _LayeredReplStatusOwner) -> FormattedText:
        telemetry = (
            self._runtime_status.telemetry_snapshot()
            if self._runtime_status is not None
            else None
        )
        toolbar = format_bottom_toolbar_text(
            hint_overrides=self.capability_hint_overrides(),
            bundle_name=self._bundle_name,
            session_id=self._session_id,
            active_mode=self._active_mode(),
            is_running=self._is_running(),
            queued_count=self._queued_count(),
            tasks_available=True,
            image_paste_available=(self._clipboard_detector.snapshot.image_available),
            session_cost=(
                telemetry.session.cost_usd if telemetry is not None else None
            ),
            trust_summary=(
                self._trust_state.active.summary()
                if self._trust_state is not None
                else None
            ),
            permission_mode=(
                self._trust_state.active.name if self._trust_state is not None else None
            ),
            last_yield=(
                self._outcome_ledger.footer_yield()
                if self._outcome_ledger is not None
                else None
            ),
            needs_attention_count=(
                self._needs_you.pending_count if self._needs_you is not None else 0
            ),
            approval_pending=self._approval_visible(),
            palette_open=self._palette_visible(),
            lane_focused=(
                self._agent_lanes is not None
                and self._agent_lanes.focused_session_id != self._session_id
            ),
            max_width=max(1, self._terminal_size()[1] - 2),
        )
        risk = bool(
            self._trust_state is not None
            and self._trust_state.active.requires_risk_treatment
        )
        if not risk:
            return _footer_fragments(toolbar, mode=self._active_mode() or "chat")
        risk_end = _risk_posture_end(toolbar, bundle_name=self._bundle_name)
        return FormattedText(
            [
                ("class:status.risk", f" {toolbar[:risk_end]}"),
                ("class:status", f"{toolbar[risk_end:]} "),
            ]
        )

    def _is_running(self: _LayeredReplStatusOwner) -> bool:
        running = bool(self._get_is_running()) if self._get_is_running else False
        if running and self._running_started_at is None:
            self._running_started_at = self._clock()
        elif not running:
            self._running_started_at = None
        return running

    def _work_visible(self: _LayeredReplStatusOwner) -> bool:
        agents_running = (
            self._task_tracker.counts().running if self._task_tracker is not None else 0
        )
        return self._is_running() or bool(agents_running)

    def _working_text(self: _LayeredReplStatusOwner) -> FormattedText:
        now = self._clock()
        elapsed = 0.0
        if self._running_started_at is not None:
            elapsed = max(0.0, now - self._running_started_at)
        tokens = 0
        cost = Decimal("0")
        cost_label = "$0.00"
        if self._runtime_status is not None:
            telemetry = self._runtime_status.telemetry_snapshot()
            turn = telemetry.turn
            tokens = turn.total_tokens
            cost = turn.cost_usd or Decimal("0")
            if turn.cost_usd is not None:
                cost_label = f"${cost:.2f}"
            elif (
                self._stream_status is not None and self._stream_status.estimated_tokens
            ):
                tokens = max(tokens, self._stream_status.estimated_tokens)
                session = telemetry.session
                if session.cost_usd is not None and session.total_tokens > 0:
                    estimate = (
                        session.cost_usd
                        * Decimal(tokens)
                        / Decimal(session.total_tokens)
                    )
                    cost_label = f"~${estimate:.2f}"
                else:
                    cost_label = "cost pending"
            elif self._is_running():
                cost_label = "cost pending"
        elif self._is_running():
            cost_label = "cost pending"
        lanes, hidden_agents = self._live_agent_lanes()
        running_agents = (
            self._task_tracker.counts().running if self._task_tracker is not None else 0
        )
        stage = self._working_stage(lanes)
        columns = max(1, self._terminal_size()[1])
        details = _working_details(
            columns=columns,
            running_agents=running_agents,
            elapsed=elapsed,
            tokens=tokens,
            cost_label=cost_label,
        )
        stage_budget = max(1, columns - get_cwidth(details) - 2)
        stage = summarize_cell_text(stage, max_cells=stage_budget)
        glyph = ("✳", "✦", "✧", "✦")[int(now * 5) % 4]
        hint_start = details.find(" · esc to interrupt")
        telemetry_details = details if hint_start < 0 else details[:hint_start]
        hint_details = "" if hint_start < 0 else details[hint_start:]
        fragments: list[tuple[str, str]] = [
            ("class:working.glyph", f"{glyph} "),
            ("class:working.title", f"{stage}{telemetry_details}"),
        ]
        if hint_details:
            fragments.append((f"class:working fg:{TOKENS['dimmer']}", hint_details))
        tree_prefixes = self._live_tree_prefixes()
        for lane in lanes:
            prefix = tree_prefixes.get(lane.session_id, "`- ")
            prefix = _terminal_tree_prefix(prefix)
            lead = f"  {prefix}● "
            budget = max(1, columns - get_cwidth(lead))
            body = lane.render_tree(max_columns=budget)
            fragments.extend(
                [
                    ("", "\n"),
                    ("class:working.tree", lead),
                    ("class:working.agent", body),
                ]
            )
        if hidden_agents:
            fragments.extend(
                [
                    ("", "\n"),
                    ("class:working.tree", "  `- "),
                    (
                        "class:working.agent",
                        f"+{hidden_agents} more running "
                        f"{'agent' if hidden_agents == 1 else 'agents'}",
                    ),
                ]
            )
        return FormattedText(fragments)

    def _working_height(self: _LayeredReplStatusOwner) -> Dimension:
        lanes, hidden_agents = self._live_agent_lanes()
        return Dimension.exact(1 + len(lanes) + int(bool(hidden_agents)))

    def _working_stage(self: _LayeredReplStatusOwner, lanes: tuple[Any, ...]) -> str:
        preview = (
            self._stream_status.preview if self._stream_status is not None else None
        )
        active_step = (
            self._task_tracker.active_step_text()
            if self._task_tracker is not None
            else None
        )
        lane_count = 0
        if lanes:
            lane_count = (
                self._task_tracker.counts().running
                if self._task_tracker
                else len(lanes)
            )
        return current_activity_label(
            lane_count=lane_count,
            active_step=active_step,
            preview_kind=preview.kind if preview is not None else None,
        )

    def _live_agent_lanes(
        self: _LayeredReplStatusOwner,
    ) -> tuple[tuple[Any, ...], int]:
        if self._agent_lanes is None:
            return (), 0
        running = tuple(
            lane
            for lane in self._agent_lanes.snapshot().lanes
            if lane.status == TaskStatus.RUNNING
        )
        total = (
            self._task_tracker.counts().running if self._task_tracker else len(running)
        )
        if len(running) <= _MAX_LIVE_AGENT_ROWS:
            return running, max(0, total - len(running))
        visible = running[: _MAX_LIVE_AGENT_ROWS - 1]
        return visible, max(0, total - len(visible))

    def _live_tree_prefixes(self: _LayeredReplStatusOwner) -> dict[str, str]:
        if self._task_tracker is None:
            return {}
        return {
            row.node.session_id: row.prefix for row in self._task_tracker.tree_rows()
        }


def current_activity_label(
    *,
    lane_count: int,
    active_step: str | None,
    preview_kind: str | None,
) -> str:
    """Single source of truth for "what's happening right now" in the bottom
    persistent status bar.

    Precedence: delegated/agent-lane activity > an active plan/tool step >
    a streaming response > a distinct idle indicator. The turn's original
    prompt/title is deliberately excluded from this precedence -- it is
    already the transcript's permanent per-turn record (the committed
    ``Plan`` block, see ``layered_repl_surfaces.commit_plan_state``);
    echoing it here too would duplicate that record verbatim in a second,
    live surface.
    """
    if lane_count > 0:
        return f"Coordinating {lane_count} {'agent' if lane_count == 1 else 'agents'}"
    if active_step:
        return active_step
    if preview_kind is not None:
        return "Responding" if preview_kind == "text" else "Thinking"
    return "working"


_FOOTER_MODES = frozenset({"chat", "plan", "brainstorm", "build", "auto", "bypass"})
_FOOTER_ATTENTION = re.compile(r"q\d+|\d+ decisions? waiting|needs-you \d+|ctrl-y")
_FOOTER_ZONE_GAP = re.compile(r"  +")


def _footer_fragments(toolbar: str, *, mode: str) -> FormattedText:
    """Colorize the plain footer per spec section 6 without changing its text."""
    gap = _FOOTER_ZONE_GAP.search(toolbar)
    left = toolbar[: gap.start()] if gap else toolbar
    hints = toolbar[gap.start() :] if gap else ""
    dimmer = f"class:status fg:{TOKENS['dimmer']}"
    fragments: list[tuple[str, str]] = [("class:status", " ")]
    for index, part in enumerate(left.split(" · ")):
        if index:
            fragments.append((dimmer, " · "))
        fragments.extend(_footer_part(part, first=index == 0, mode=mode))
    if hints:
        fragments.append((dimmer, hints))
    fragments.append(("class:status", " "))
    return FormattedText(fragments)


def _footer_part(part: str, *, first: bool, mode: str) -> list[tuple[str, str]]:
    if first and part.removeprefix("mode ") == mode and mode in _FOOTER_MODES:
        return [(f"class:status class:mode.{mode}", part)]
    if part.endswith("▲"):
        return [
            ("class:status", part[:-1]),
            (f"class:status fg:{TOKENS['green']}", "▲"),
        ]
    if _FOOTER_ATTENTION.fullmatch(part):
        return [(f"class:status fg:{TOKENS['orange']}", part)]
    return [("class:status", part)]


def _risk_posture_end(toolbar: str, *, bundle_name: str) -> int:
    """Find the boundary between risky mode/trust state and neutral metadata."""
    bundle = str(bundle_name).removeprefix("bundle:").strip() or "unknown"
    for candidate in dict.fromkeys(bundle[:limit] for limit in (24, 14, 10, 5)):
        marker = f" · {candidate} ·"
        boundary = toolbar.find(marker)
        if boundary > 0:
            return boundary
    cost_boundary = toolbar.find(" · $")
    if cost_boundary > 0:
        return cost_boundary
    separator = toolbar.find(" · ")
    return separator if separator >= 0 else len(toolbar)


def _terminal_tree_prefix(prefix: str) -> str:
    return prefix.replace("|  ", "│  ").replace("|- ", "├─ ").replace("`- ", "└─ ")


def _working_details(
    *,
    columns: int,
    running_agents: int,
    elapsed: float,
    tokens: int,
    cost_label: str,
) -> str:
    parts: list[tuple[str, str]] = [
        ("elapsed", format_elapsed(elapsed)),
        ("tokens", f"↓ {format_tokens(tokens)} tok"),
    ]
    if running_agents:
        parts.append(
            (
                "agents",
                f"{running_agents} {'agent' if running_agents == 1 else 'agents'}",
            )
        )
    parts.extend(
        (
            ("cost", cost_label),
            ("interrupt", "esc to interrupt"),
            ("steer", "type to steer"),
        )
    )
    minimum_stage = min(20, max(7, columns // 3))
    removable = ("steer", "interrupt", "tokens", "agents", "cost")
    while parts:
        details = "".join(f" · {value}" for _, value in parts)
        if get_cwidth(details) <= max(0, columns - minimum_stage - 2):
            return details
        key = next(
            (item for item in removable if any(k == item for k, _ in parts)), None
        )
        if key is None:
            break
        parts = [item for item in parts if item[0] != key]
    return "".join(f" · {value}" for _, value in parts)


def queued_bar_text(
    *, count: int, previews: tuple[str, ...], columns: int
) -> FormattedText:
    """Render the queued-next bar per spec section 5: quote the first message."""
    if previews:
        suffix = f" (+{count - 1} more)" if count > 1 else ""
        budget = max(1, columns - 60 - get_cwidth(suffix))
        preview = f'"{summarize_cell_text(previews[0], max_cells=budget)}"{suffix}'
    else:
        preview = summarize_cell_text(
            f"{count} message(s)", max_cells=max(1, columns - 60)
        )
    return FormattedText(
        [
            (
                "class:queued",
                f"  ▹ queued next: {preview} · runs when this turn ends",
            ),
            (f"class:queued fg:{TOKENS['dimmer']}", " · alt+up edit"),
        ]
    )


def format_tokens(tokens: int) -> str:
    if tokens < 1_000:
        return str(tokens)
    if tokens < 1_000_000:
        return f"{tokens / 1_000:.1f}k"
    return f"{tokens / 1_000_000:.1f}m"


__all__ = [
    "LayeredReplStatusMixin",
    "current_activity_label",
    "format_tokens",
    "queued_bar_text",
]
