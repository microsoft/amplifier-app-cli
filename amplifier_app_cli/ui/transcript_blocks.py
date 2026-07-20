"""Typed transcript blocks and the canonical terminal renderer."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from math import isfinite
from typing import TypeAlias

from rich.cells import cell_len
from rich.console import Console
from rich.rule import Rule
from rich.syntax import Syntax
from rich.text import Text

from ..console import Markdown
from .layered_repl_style import TOKENS
from .runtime_values import ToolActivitySnapshot
from .runtime_values import ToolActivityStatus
from .runtime_values import UsageTotalsSnapshot
from .text_paste import compact_text_paste_display

_MAX_TEXT_CHARS = 32_768
_MAX_COMMAND_CHARS = 8_192
_MAX_DEBUG_LINES = 2_000
_MAX_PLAN_ITEMS = 100
_MAX_DIFF_LINES = 400
_MAX_PATH_CHARS = 500
_TOOL_OUTPUT_HEAD_LINES = 8
_TOOL_OUTPUT_TAIL_LINES = 4

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")

_FG = TOKENS["fg"]
_FG_BRIGHT = TOKENS["bright"]
_DIM = TOKENS["dim"]
_DIMMER = TOKENS["dimmer"]
_GREEN = TOKENS["green"]
_ORANGE = TOKENS["orange"]
_RED = TOKENS["red"]
_TEAL = TOKENS["teal"]
_BLUE = TOKENS["blue"]
_RULE = TOKENS["rule"]

_MODE_STYLES = {
    "chat": _DIM,
    "plan": _BLUE,
    "brainstorm": _TEAL,
    "build": _GREEN,
    "auto": _ORANGE,
    "bypass": _RED,
}


def _safe_text(value: object, *, limit: int = _MAX_TEXT_CHARS) -> str:
    text = str(value)
    text = "".join(
        character
        for character in text
        if character in {"\n", "\t"} or ord(character) >= 32
    )
    return text[:limit]


def _single_line(value: object, *, limit: int = _MAX_TEXT_CHARS) -> str:
    return " ".join(_safe_text(value, limit=limit).split())


def _format_elapsed(seconds: float) -> str:
    if seconds < 10:
        return f"{seconds:.1f}s"
    if seconds < 60:
        return f"{round(seconds)}s"
    minutes, remainder = divmod(round(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {remainder:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def _format_tokens(tokens: int) -> str:
    if tokens < 1_000:
        return str(tokens)
    if tokens < 1_000_000:
        return f"{tokens / 1_000:.1f}k"
    return f"{tokens / 1_000_000:.1f}m"


@dataclass(frozen=True, slots=True)
class Telemetry:
    """Compact turn or session telemetry shown only as a suffix."""

    elapsed_seconds: float | None = None
    tokens: int | None = None
    cached_percent: int | None = None
    cost: Decimal | float | str | None = None

    def __post_init__(self) -> None:
        if self.elapsed_seconds is not None and (
            not isfinite(self.elapsed_seconds) or self.elapsed_seconds < 0
        ):
            raise ValueError("elapsed_seconds must be finite and non-negative")
        if self.tokens is not None and self.tokens < 0:
            raise ValueError("tokens must be non-negative")
        if self.cached_percent is not None and not 0 <= self.cached_percent <= 100:
            raise ValueError("cached_percent must be between 0 and 100")
        if self.cost is not None:
            try:
                cost = Decimal(str(self.cost))
            except (InvalidOperation, ValueError) as error:
                raise ValueError(
                    "cost must be a finite non-negative decimal"
                ) from error
            if not cost.is_finite() or cost < 0:
                raise ValueError("cost must be a finite non-negative decimal")
            object.__setattr__(self, "cost", cost)

    def _parts(self, *, token_arrow: bool) -> list[str]:
        parts: list[str] = []
        if self.elapsed_seconds is not None:
            parts.append(_format_elapsed(self.elapsed_seconds))
        if self.tokens is not None:
            token_part = f"{_format_tokens(self.tokens)} tok"
            if token_arrow:
                token_part = f"↓ {token_part}"
            if self.cached_percent is not None:
                token_part += f", {self.cached_percent}% cached"
            parts.append(token_part)
        if self.cost is not None:
            parts.append(f"${self.cost:.2f}")
        return parts

    def suffix(self) -> str:
        parts = self._parts(token_arrow=True)
        return f"({' · '.join(parts)})" if parts else ""

    def label(self) -> str:
        """Bare turn-rule label: `<secs>s · <tok>k tok, <cache>% cached · $<cost>`."""
        return " · ".join(self._parts(token_arrow=False))


class ToolStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class PlanItemStatus(str, Enum):
    COMPLETED = "completed"
    ACTIVE = "active"
    PENDING = "pending"


@dataclass(frozen=True, slots=True)
class UserBlock:
    text: str
    mode: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", _safe_text(self.text))
        if self.mode is not None:
            object.__setattr__(self, "mode", _single_line(self.mode, limit=32))


@dataclass(frozen=True, slots=True)
class AnswerBlock:
    markdown: str
    label: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "markdown", _safe_text(self.markdown))
        if self.label is not None:
            object.__setattr__(self, "label", _single_line(self.label, limit=80))


@dataclass(frozen=True, slots=True)
class SessionHeaderBlock:
    """Subdued startup identity that is distinct from agent narration."""

    headline: str
    detail: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "headline", _single_line(self.headline, limit=240))
        object.__setattr__(self, "detail", _single_line(self.detail, limit=500))


@dataclass(frozen=True, slots=True)
class NarrationBlock:
    text: str
    telemetry: Telemetry | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", _single_line(self.text))


@dataclass(frozen=True, slots=True)
class ToolBlock:
    summary: str
    status: ToolStatus
    command: str = ""
    output: tuple[str, ...] = ()
    expanded: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "summary", _single_line(self.summary))
        object.__setattr__(
            self, "command", _safe_text(self.command, limit=_MAX_COMMAND_CHARS)
        )
        output = tuple(_safe_text(line) for line in self.output[:_MAX_DEBUG_LINES])
        object.__setattr__(self, "output", output)


@dataclass(frozen=True, slots=True)
class BlockedBlock:
    action: str
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", _single_line(self.action))
        object.__setattr__(self, "reason", _single_line(self.reason))


@dataclass(frozen=True, slots=True)
class CodeExcerptBlock:
    code: str
    language: str = "text"
    start_line: int = 1
    changed_lines: frozenset[int] = frozenset()

    def __post_init__(self) -> None:
        if self.start_line < 1:
            raise ValueError("start_line must be positive")
        object.__setattr__(self, "code", _safe_text(self.code))
        object.__setattr__(
            self, "language", _single_line(self.language, limit=40) or "text"
        )
        if any(line < self.start_line for line in self.changed_lines):
            raise ValueError("changed_lines cannot precede start_line")


@dataclass(frozen=True, slots=True)
class DiffBlock:
    """One file's unified diff hunks with add/remove counts.

    ``diff_text`` carries hunk lines only (``@@`` headers, ``+``/``-``/context
    lines); file headers stay in the ``path``/``move_path`` fields. Lines that
    are not diff syntax (parser notes, ``\\ No newline at end of file``) render
    as dim annotations without gutter numbers.
    """

    path: str
    diff_text: str
    added: int
    removed: int
    move_path: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _single_line(self.path, limit=_MAX_PATH_CHARS))
        lines = _safe_text(self.diff_text).splitlines()
        object.__setattr__(self, "diff_text", "\n".join(lines[:_MAX_DIFF_LINES]))
        if self.added < 0 or self.removed < 0:
            raise ValueError("added and removed counts must be non-negative")
        if self.move_path is not None:
            object.__setattr__(
                self, "move_path", _single_line(self.move_path, limit=_MAX_PATH_CHARS)
            )


@dataclass(frozen=True, slots=True)
class PlanItem:
    text: str
    status: PlanItemStatus = PlanItemStatus.PENDING

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", _single_line(self.text))


@dataclass(frozen=True, slots=True)
class PlanBlock:
    title: str
    items: tuple[PlanItem, ...]
    telemetry: Telemetry | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", _single_line(self.title))
        object.__setattr__(self, "items", tuple(self.items[:_MAX_PLAN_ITEMS]))


@dataclass(frozen=True, slots=True)
class StatusBlock:
    telemetry: Telemetry
    interrupt_hint: str = "esc to interrupt"
    steering_hint: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "interrupt_hint", _single_line(self.interrupt_hint, limit=80)
        )
        if self.steering_hint is not None:
            object.__setattr__(
                self, "steering_hint", _single_line(self.steering_hint, limit=80)
            )


@dataclass(frozen=True, slots=True)
class RecapBlock:
    goal: str
    next_action: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "goal", _single_line(self.goal))
        object.__setattr__(self, "next_action", _single_line(self.next_action))


@dataclass(frozen=True, slots=True)
class DebugBlock:
    lines: tuple[str, ...]
    label: str = "Debug"
    expanded: bool = False
    total_lines: int | None = None

    def __post_init__(self) -> None:
        source_lines = tuple(self.lines)
        object.__setattr__(
            self,
            "lines",
            tuple(_safe_text(line) for line in source_lines[:_MAX_DEBUG_LINES]),
        )
        object.__setattr__(self, "label", _single_line(self.label, limit=80))
        total_lines = self.total_lines
        if total_lines is None:
            total_lines = len(source_lines)
        object.__setattr__(self, "total_lines", max(len(self.lines), total_lines))


@dataclass(frozen=True, slots=True)
class TurnTerminatorBlock:
    telemetry: Telemetry
    outcome: str = ""
    shipped: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcome", _single_line(self.outcome, limit=240))


TranscriptBlock: TypeAlias = (
    UserBlock
    | AnswerBlock
    | SessionHeaderBlock
    | NarrationBlock
    | ToolBlock
    | BlockedBlock
    | CodeExcerptBlock
    | DiffBlock
    | PlanBlock
    | StatusBlock
    | RecapBlock
    | DebugBlock
    | TurnTerminatorBlock
)


class TranscriptRenderer:
    """Render every immutable transcript element through one block grammar."""

    def __init__(
        self,
        console: Console,
        render_profile: str | Callable[[], str] | None = None,
        show_debug: bool | Callable[[], bool] = False,
    ) -> None:
        self.console = console
        self._render_profile = render_profile
        self._show_debug = show_debug

    def render(self, block: TranscriptBlock) -> None:
        profile = (
            self._render_profile()
            if callable(self._render_profile)
            else self._render_profile
        )
        hidden = (ToolBlock, CodeExcerptBlock, DiffBlock, DebugBlock)
        if profile == "plan" and isinstance(block, hidden):
            return
        if profile == "divergent" and isinstance(block, (*hidden, PlanBlock)):
            return
        method_name = f"_render_{type(block).__name__.removesuffix('Block').lower()}"
        renderer = getattr(self, method_name, None)
        if renderer is None:
            raise TypeError(f"Unsupported transcript block: {type(block).__name__}")
        renderer(block)

    def _render_user(self, block: UserBlock) -> None:
        line = Text("\n❯ ", style=f"bold {_GREEN}")
        if block.mode:
            line.append(
                f"[{block.mode}] ",
                style=_MODE_STYLES.get(block.mode.casefold(), _DIM),
            )
        line.append(compact_text_paste_display(block.text), style=_FG_BRIGHT)
        self.console.print(line)

    def _render_answer(self, block: AnswerBlock) -> None:
        if block.label:
            self.console.print(Text(f"\n{block.label}:", style=f"bold {_GREEN}"))
        self.console.print(Markdown(block.markdown))

    def _render_sessionheader(self, block: SessionHeaderBlock) -> None:
        self.console.print(Text(block.headline, style=f"bold {_FG_BRIGHT}"))
        if block.detail:
            self.console.print(Text(block.detail, style=_DIM))

    def _render_narration(self, block: NarrationBlock) -> None:
        line = Text("● ", style=_FG_BRIGHT)
        line.append(block.text, style=_FG)
        self._append_telemetry(line, block.telemetry)
        self.console.print(line)

    def _render_tool(self, block: ToolBlock) -> None:
        if block.status == ToolStatus.BLOCKED:
            self._render_blocked(
                BlockedBlock(f"blocked · {block.summary}", "finding safer path")
            )
            return
        summary_style = _RED if block.status == ToolStatus.FAILED else _DIM
        summary = Text("  ● ", style=summary_style)
        summary.append(block.summary, style=summary_style)
        if block.output and not block.expanded:
            summary.append(" · click or ctrl-o expand", style=_DIMMER)
        self.console.print(summary)
        if block.status == ToolStatus.RUNNING and block.command:
            command = Text("  └ ", style=_DIMMER)
            command.append(f"$ {block.command}", style=_DIM)
            self.console.print(command)
        if block.expanded:
            self._render_tool_output(block.output)

    def _render_tool_output(self, output: tuple[str, ...]) -> None:
        """Print an expanded tool body, eliding the middle of long output.

        Head/tail elision (after codex ``output_lines``): the first
        ``_TOOL_OUTPUT_HEAD_LINES`` and last ``_TOOL_OUTPUT_TAIL_LINES`` lines
        stay, with an accounting line for the omitted middle — the same
        omitted-line accounting DebugBlock reports.
        """
        omitted = len(output) - _TOOL_OUTPUT_HEAD_LINES - _TOOL_OUTPUT_TAIL_LINES
        head = output[:_TOOL_OUTPUT_HEAD_LINES] if omitted > 0 else output
        tail = output[len(output) - _TOOL_OUTPUT_TAIL_LINES :] if omitted > 0 else ()
        for line in head:
            self.console.print(Text(f"      {line}", style=_DIMMER))
        if omitted > 0:
            self.console.print(
                Text(
                    f"      … +{omitted} lines · full via ctrl-o again "
                    "or transcript export",
                    style=_DIM,
                )
            )
        for line in tail:
            self.console.print(Text(f"      {line}", style=_DIMMER))

    def _render_blocked(self, block: BlockedBlock) -> None:
        line = Text("  ⊘ ", style=_RED)
        line.append(block.action, style=_RED)
        if block.reason:
            line.append(f" · {block.reason}", style=_DIM)
        self.console.print(line)

    def _render_codeexcerpt(self, block: CodeExcerptBlock) -> None:
        self.console.print(
            Syntax(
                block.code,
                block.language,
                line_numbers=True,
                start_line=block.start_line,
                highlight_lines=set(block.changed_lines),
                word_wrap=True,
                background_color="default",
            )
        )

    def _render_diff(self, block: DiffBlock) -> None:
        header = Text("· ", style=_DIM)
        header.append(block.path, style=_FG)
        if block.move_path:
            header.append(" → ", style=_DIM)
            header.append(block.move_path, style=_FG)
        header.append(" (", style=_DIM)
        header.append(f"+{block.added}", style=_GREEN)
        header.append(" ", style=_DIM)
        header.append(f"−{block.removed}", style=_RED)
        header.append(")", style=_DIM)
        self.console.print(header)
        gutter_blank = f"  {'':>4} "
        old_line = new_line = 0
        in_hunk = False
        for line in block.diff_text.splitlines():
            hunk = _HUNK_HEADER.match(line)
            if hunk is not None:
                old_line, new_line = int(hunk.group(1)), int(hunk.group(2))
                in_hunk = True
                self.console.print(Text(f"{gutter_blank}{line}", style=_DIMMER))
                continue
            if not in_hunk or line.startswith("\\"):
                self.console.print(Text(f"{gutter_blank}{line}", style=_DIMMER))
                continue
            if line.startswith("+"):
                rendered = Text(f"  {new_line:>4} ", style=_DIMMER)
                rendered.append(f"+{line[1:]}", style=_GREEN)
                new_line += 1
            elif line.startswith("-"):
                rendered = Text(f"  {old_line:>4} ", style=_DIMMER)
                rendered.append(f"−{line[1:]}", style=_RED)
                old_line += 1
            else:
                content = line[1:] if line.startswith(" ") else line
                rendered = Text(f"  {new_line:>4} ", style=_DIMMER)
                rendered.append(f" {content}", style=_FG)
                old_line += 1
                new_line += 1
            self.console.print(rendered)

    def _render_plan(self, block: PlanBlock) -> None:
        header = Text("· ", style=_ORANGE)
        header.append(block.title, style=_FG)
        self._append_telemetry(header, block.telemetry)
        self.console.print(header)
        styles = {
            PlanItemStatus.COMPLETED: ("✔", _GREEN, _DIM),
            PlanItemStatus.ACTIVE: ("■", _ORANGE, f"bold {_FG_BRIGHT}"),
            PlanItemStatus.PENDING: ("□", _DIMMER, _DIM),
        }
        for item in block.items:
            glyph, glyph_style, text_style = styles[item.status]
            line = Text(f"  {glyph} ", style=glyph_style)
            line.append(item.text, style=text_style)
            self.console.print(line)

    def _render_status(self, block: StatusBlock) -> None:
        line = Text("✳ ", style=_ORANGE)
        line.append("working", style=_DIM)
        suffix = block.telemetry.suffix()
        if suffix:
            line.append(f" · {suffix[1:-1]}", style=_DIM)
        if block.interrupt_hint:
            line.append(f" · {block.interrupt_hint}", style=_DIMMER)
        if block.steering_hint:
            line.append(f" · {block.steering_hint}", style=_DIMMER)
        self.console.print(line)

    def _render_recap(self, block: RecapBlock) -> None:
        line = Text("✳ ", style=_DIMMER)
        line.append(
            f"Goal: {block.goal}. Next: {block.next_action}.", style=f"italic {_DIM}"
        )
        self.console.print(line)

    def _render_debug(self, block: DebugBlock) -> None:
        always_show = (
            self._show_debug() if callable(self._show_debug) else self._show_debug
        )
        total_lines = block.total_lines or len(block.lines)
        if not block.expanded and not always_show:
            self.console.print(
                Text(f"  ({total_lines} lines · ctrl-o expand)", style=_DIMMER)
            )
            return
        self.console.print(Text(f"{block.label}:", style=f"italic {_DIM}"))
        for line in block.lines:
            self.console.print(Text(line, style=f"italic {_DIM}"))
        omitted_lines = max(0, total_lines - len(block.lines))
        if omitted_lines:
            self.console.print(
                Text(
                    f"... {omitted_lines} additional lines omitted "
                    f"({total_lines} total)",
                    style=f"italic {_DIMMER}",
                )
            )

    def _render_turnterminator(self, block: TurnTerminatorBlock) -> None:
        title = " · ".join(
            part for part in (block.telemetry.label(), block.outcome) if part
        )
        label_style = _DIM if block.shipped else _DIMMER
        if cell_len(title) + 4 <= self.console.width:
            self.console.print(
                Rule(title=Text(title, style=label_style), align="right", style=_RULE)
            )
            return
        self.console.print(Rule(style=_RULE))
        self.console.print(
            Text(title, style=label_style, justify="right", overflow="fold")
        )

    @staticmethod
    def _append_telemetry(line: Text, telemetry: Telemetry | None) -> None:
        if telemetry is None:
            return
        suffix = telemetry.suffix()
        if suffix:
            line.append(f"  {suffix}", style=_DIM)


def telemetry_from_usage(usage: UsageTotalsSnapshot) -> Telemetry:
    """Adapt canonical runtime usage into the transcript telemetry suffix."""
    return Telemetry(
        elapsed_seconds=usage.duration_seconds,
        tokens=usage.total_tokens,
        cached_percent=usage.cache_percent,
        cost=usage.cost_usd,
    )


def tool_block_from_activity(
    activity: ToolActivitySnapshot, *, expanded: bool = False
) -> ToolBlock:
    """Adapt a runtime tool lifecycle snapshot into the fixed block grammar."""
    status = {
        ToolActivityStatus.RUNNING: ToolStatus.RUNNING,
        ToolActivityStatus.SUCCEEDED: ToolStatus.COMPLETED,
        ToolActivityStatus.FAILED: ToolStatus.FAILED,
    }[activity.status]
    verb = "Running" if status == ToolStatus.RUNNING else "Ran"
    if status == ToolStatus.RUNNING:
        summary = activity.summary or f"Running {activity.tool_name}"
    elif status == ToolStatus.FAILED:
        summary = f"{activity.tool_name} failed"
    elif activity.tool_name.lower() in {"shell", "bash", "exec", "exec_command"}:
        summary = "Ran 1 shell command"
    else:
        summary = f"{verb} 1 {activity.tool_name} call"
    output: tuple[str, ...] = ()
    if activity.result is not None and activity.result.preview:
        output = tuple(activity.result.preview.splitlines())
        if activity.result.truncated:
            output += ("... output truncated",)
    return ToolBlock(
        summary=summary,
        status=status,
        command=activity.command,
        output=output,
        expanded=expanded,
    )


__all__ = [
    "AnswerBlock",
    "BlockedBlock",
    "CodeExcerptBlock",
    "DebugBlock",
    "DiffBlock",
    "NarrationBlock",
    "PlanBlock",
    "PlanItem",
    "PlanItemStatus",
    "RecapBlock",
    "SessionHeaderBlock",
    "StatusBlock",
    "Telemetry",
    "telemetry_from_usage",
    "ToolBlock",
    "ToolStatus",
    "tool_block_from_activity",
    "TranscriptBlock",
    "TranscriptRenderer",
    "TurnTerminatorBlock",
    "UserBlock",
]
