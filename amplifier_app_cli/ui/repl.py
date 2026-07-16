"""Prompt-toolkit helpers for the interactive Amplifier REPL."""

from __future__ import annotations

import html
import logging
import re
from collections.abc import Callable, Iterable
from pathlib import Path
from time import monotonic
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer
from prompt_toolkit.completion import Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth
from rich.markup import escape

from .command_palette import CommandPalette
from .command_registry import CommandRegistry
from .command_registry import CompletionProvider
from .command_registry import compose_command_registry
from .footer import format_bottom_toolbar_html as format_bottom_toolbar_html
from .footer import format_bottom_toolbar_text as format_bottom_toolbar_text
from .layered_repl_style import TOKENS
from .task_pane import format_task_pane_text as format_task_pane_text

logger = logging.getLogger(__name__)

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]+")

# Most terminals silently truncate titles beyond a few hundred characters;
# 240 keeps titles readable in tab bars (mirrors codex terminal_title.rs).
_TITLE_MAX_CHARS = 240

# Trojan-Source bidi controls plus invisible formatting codepoints that could
# visually reorder or hide title text relative to its underlying bytes. This
# is the aggressive, titles-only set (codex terminal_title.rs): unlike the
# shared runtime_values.sanitize(), it also drops ZWJ/ZWNJ and variation
# selectors because emoji fidelity does not matter in a window title.
_TITLE_DISALLOWED_CODEPOINTS = frozenset(
    {
        0x00AD,  # soft hyphen
        0x034F,  # combining grapheme joiner
        0x061C,  # Arabic letter mark
        0x180E,  # Mongolian vowel separator
        0xFEFF,  # BOM / zero-width no-break space
        *range(0x200B, 0x2010),  # ZWSP, ZWNJ, ZWJ, LRM, RLM
        *range(0x202A, 0x202F),  # bidi embeddings/overrides (Trojan Source)
        *range(0x2060, 0x2070),  # word joiner, invisible operators, isolates
        *range(0xFE00, 0xFE10),  # variation selectors
        *range(0xFFF9, 0xFFFC),  # interlinear annotation controls
        *range(0x1BCA0, 0x1BCA4),  # shorthand format controls
        *range(0xE0000, 0xE0080),  # astral tag characters
        *range(0xE0100, 0xE01F0),  # variation selectors supplement
    }
)

_TITLE_SPINNER = ("✳", "✦", "✧", "✦")


def supports_layered_ui(input_stream: Any, output_stream: Any) -> bool:
    """Return whether both sides of the interactive UI are attached to a TTY."""
    for stream in (input_stream, output_stream):
        try:
            if not stream.isatty():
                return False
        except (AttributeError, OSError, ValueError):
            return False
    return True


class SlashCommandCompleter(Completer):
    """Complete Amplifier slash commands without touching prompt text input."""

    def __init__(
        self,
        commands: CommandRegistry | dict[str, dict[str, Any]],
        *,
        mode_shortcuts: dict[str, Any] | None = None,
        skill_shortcuts: dict[str, Any] | None = None,
        mcp_prompts: list[tuple[str, str, str]] | tuple[tuple[str, str, str], ...] = (),
        mode_names: list[str] | None = None,
        skill_names: list[str] | None = None,
        model_names: Iterable[str] | Callable[[], Iterable[str]] | None = None,
    ):
        self.mode_shortcuts = mode_shortcuts or {}
        self.skill_shortcuts = skill_shortcuts or {}
        self.mode_names = sorted(set(mode_names or []) | set(self.mode_shortcuts))
        self.skill_names = sorted(set(skill_names or []))
        self._model_names = model_names
        self.registry = compose_command_registry(
            commands,
            mode_shortcuts=self.mode_shortcuts,
            skill_shortcuts=self.skill_shortcuts,
            mcp_prompts=mcp_prompts,
        )
        self.commands = self.registry.legacy_metadata()
        self.palette = CommandPalette.from_registry(self.registry)

    def get_completions(self, document: Document, complete_event):
        text_before = document.text_before_cursor
        if not text_before.startswith("/"):
            return

        if " " in text_before:
            command = text_before.split(maxsplit=1)[0]
            spec = self.registry.resolve(command)
            if spec is None or spec.completion is None:
                return
            options = list(spec.completion.values)
            provider = spec.completion.provider
            if provider is CompletionProvider.MODE:
                options.extend(self._mode_options())
            elif provider is CompletionProvider.MODEL:
                options.extend(self._model_options())
            elif provider is CompletionProvider.SKILL:
                options.extend(self.skill_names)
            yield from self._complete_word(
                text_before,
                options,
                provider.value if provider is not None else "command option",
            )
            return

        snapshot = self.palette.query(text_before)
        for command in snapshot.commands:
            yield Completion(
                command.name,
                start_position=-len(text_before),
                display=command.name,
                display_meta=f"{command.source.value} · {command.description}",
            )

    def _mode_options(self) -> list[str]:
        return sorted(set(self.mode_names) | {"off", "info"})

    def _model_options(self) -> list[str]:
        source = (
            self._model_names() if callable(self._model_names) else self._model_names
        )
        return sorted({str(name) for name in source or () if str(name).strip()})

    def _complete_word(self, text_before: str, options: list[str], meta: str):
        token = text_before.rsplit(" ", maxsplit=1)[-1]
        start_position = -len(token) if token else 0
        prefix = token.lower()
        for option in sorted(set(options)):
            if option.lower().startswith(prefix):
                yield Completion(
                    option,
                    start_position=start_position,
                    display=option,
                    display_meta=meta,
                )


def format_prompt_text(active_mode: str | None = None) -> HTML:
    """Return the REPL prompt with optional mode context."""
    if active_mode:
        safe_mode = html.escape(active_mode)
        return HTML(
            "\n<ansigreen><b>amplifier</b></ansigreen> "
            f"<ansicyan>[{safe_mode}]</ansicyan> "
            "<ansigreen><b>></b></ansigreen> "
        )
    return HTML(
        "\n<ansigreen><b>amplifier</b></ansigreen> <ansigreen><b>></b></ansigreen> "
    )


def _collapse_display_text(text: str) -> str:
    """Collapse whitespace/control characters into one display-safe line."""
    collapsed = " ".join(str(text).split())
    return _CONTROL_CHARS.sub(" ", collapsed).strip()


def summarize_text(text: str, *, max_chars: int = 72) -> str:
    """Return a single-line display summary without control characters."""
    collapsed = _collapse_display_text(text)
    if not collapsed:
        return "chat"
    if len(collapsed) <= max_chars:
        return collapsed
    return collapsed[: max_chars - 3].rstrip() + "..."


def format_task_title(text: str, *, max_chars: int = 72) -> str:
    """Return a quoted excerpt of ``text`` for use as a turn's task-title label.

    This is deliberately *not* a summary or a generated title -- it is a
    verbatim excerpt of the user's own prompt, quoted (matching the
    convention ``queued_bar_text`` already uses for queued-message previews)
    so it reads as "here is what you asked" rather than an unmarked echo
    that could be mistaken for something the system generated. Truncation
    backs off to the previous whole word so long prompts never end mid-word.

    Used as the single source for every place a turn's title is displayed:
    the live working status, the plan pane, and the transcript's committed
    plan/recap records.
    """
    collapsed = _collapse_display_text(text)
    if not collapsed:
        return '"chat"'
    if len(collapsed) <= max_chars:
        return f'"{collapsed}"'
    budget = max(1, max_chars - 3)
    truncated = collapsed[:budget]
    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0]
    return f'"{truncated.rstrip()}..."'


def summarize_cell_text(text: str, *, max_cells: int) -> str:
    """Truncate display text by terminal cells rather than code points."""
    collapsed = " ".join(str(text).split()).strip() or "chat"
    if get_cwidth(collapsed) <= max_cells:
        return collapsed
    suffix = "..." if max_cells >= 4 else ""
    budget = max(0, max_cells - len(suffix))
    result = ""
    for char in collapsed:
        if get_cwidth(result + char) > budget:
            break
        result += char
    return result.rstrip() + suffix


def format_elapsed(seconds: float) -> str:
    """Format elapsed seconds for compact transcript status lines."""
    if seconds < 10:
        return f"{seconds:.1f}s"
    if seconds < 60:
        return f"{round(seconds)}s"
    minutes, remainder = divmod(round(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {remainder:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def format_activity_start(prompt_text: str) -> str:
    """Return a compact transcript line for the start of model work."""
    summary = escape(summarize_text(prompt_text))
    return (
        f"\n[dim]Working:[/dim] {summary}\n"
        "[dim]Ctrl+C stops after the current operation; press again to force.[/dim]"
    )


def format_activity_result(status: str, elapsed_seconds: float) -> str:
    """Return a compact transcript line for completion or cancellation."""
    elapsed = format_elapsed(elapsed_seconds)
    if status == "cancelled":
        return f"\n[yellow]Cancelled after {elapsed}[/yellow]"
    return f"\n[dim]Done in {elapsed}[/dim]"


def format_queue_added(prompt_text: str, queued_count: int) -> str:
    """Return a compact transcript line when input is queued mid-turn."""
    summary = escape(summarize_text(prompt_text))
    suffix = "message" if queued_count == 1 else "messages"
    return (
        f"\n[dim]Queued:[/dim] {summary} [dim]({queued_count} {suffix} waiting)[/dim]"
    )


def build_terminal_title(
    *,
    cwd: Path | str,
    bundle_name: str,
    session_id: str | None,
    active_mode: str | None = None,
    task_summary: str | None = None,
    is_running: bool = False,
    agent_count: int = 0,
    needs_count: int = 0,
) -> str:
    """Build a terminal tab title for the current Amplifier session (spec 7)."""
    del active_mode, agent_count, needs_count  # spec section 7 drops these segments
    cwd_path = Path(cwd)
    project = cwd_path.name or str(cwd_path)
    if is_running:
        activity = (
            summarize_text(task_summary, max_chars=52) if task_summary else "working"
        )
    else:
        activity = "ready"
    parts = [
        project,
        "Amplifier",
        activity,
        bundle_name.removeprefix("bundle:") or "unknown",
    ]
    if session_id:
        parts.append(session_id[:8])
    title = " — ".join(parts)
    if is_running:
        title = f"{_TITLE_SPINNER[int(monotonic() * 5) % 4]} {title}"
    return _sanitize_terminal_title(title)


def terminal_title_sequence(title: str) -> str:
    """Return the OSC sequence that sets a terminal title."""
    return f"\033]0;{_sanitize_terminal_title(title)}\a"


def terminal_tab_color_sequence(state: str) -> str:
    """Return iTerm-compatible OSC tab color controls for ambient state."""
    colors = {
        "running": _token_rgb("orange"),
        "needs-you": _token_rgb("red"),
    }
    if state not in colors:
        return "\033]6;1;bg;*;default\a"
    red, green, blue = colors[state]
    return "".join(
        (
            f"\033]6;1;bg;red;brightness;{red}\a",
            f"\033]6;1;bg;green;brightness;{green}\a",
            f"\033]6;1;bg;blue;brightness;{blue}\a",
        )
    )


def _token_rgb(token: str) -> tuple[int, int, int]:
    """Parse a theme token's hex value into an RGB tuple."""
    value = TOKENS[token].lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def terminal_notification_sequence(title: str, body: str) -> str:
    """Return a bounded OSC notification without allowing escape injection."""
    safe_title = _sanitize_terminal_title(title)[:80]
    safe_body = _sanitize_terminal_title(body)[:240]
    return f"\033]777;notify;{safe_title};{safe_body}\a"


def emit_terminal_title(console: Any, title: str) -> None:
    """Set the terminal title when the output stream is an interactive terminal."""
    if not getattr(console, "is_terminal", False):
        return
    file = getattr(console, "file", None)
    if file is None or not hasattr(file, "write"):
        return
    file.write(terminal_title_sequence(title))
    flush = getattr(file, "flush", None)
    if callable(flush):
        flush()


def _sanitize_terminal_title(title: str) -> str:
    """Normalize untrusted title text into a single bounded display line.

    Replaces terminal control characters, drops Trojan-Source bidi controls
    and invisible formatting codepoints, collapses whitespace runs, and caps
    the result at ``_TITLE_MAX_CHARS`` characters.
    """
    text = _CONTROL_CHARS.sub(" ", str(title))
    visible = "".join(
        char for char in text if ord(char) not in _TITLE_DISALLOWED_CODEPOINTS
    )
    return " ".join(visible.split())[:_TITLE_MAX_CHARS].rstrip()


def create_prompt_session(
    *,
    history_path: Path,
    commands: dict[str, dict[str, Any]],
    get_active_mode: Callable[[], str | None] | None = None,
    get_is_running: Callable[[], bool] | None = None,
    get_queued_count: Callable[[], int] | None = None,
    mode_shortcuts: dict[str, Any] | None = None,
    skill_shortcuts: dict[str, Any] | None = None,
    mcp_prompts: tuple[tuple[str, str, str], ...] = (),
    mode_names: list[str] | None = None,
    skill_names: list[str] | None = None,
    model_names: Iterable[str] | Callable[[], Iterable[str]] | None = None,
    bundle_name: str = "unknown",
    session_id: str | None = None,
    on_interrupt: Callable[[], bool] | None = None,
) -> PromptSession:
    """Create a prompt-toolkit session for Amplifier's interactive chat."""
    history_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        history = FileHistory(str(history_path))
    except OSError as e:
        history = InMemoryHistory()
        logger.warning(
            "Could not load history from %s: %s. Using in-memory history.",
            history_path,
            e,
        )

    key_bindings = KeyBindings()

    @key_bindings.add("c-j")
    def insert_newline(event):
        event.current_buffer.insert_text("\n")

    @key_bindings.add("enter")
    def accept_input(event):
        event.current_buffer.validate_and_handle()

    @key_bindings.add("c-c")
    def handle_interrupt(event):
        if on_interrupt and on_interrupt():
            event.app.invalidate()
            return
        event.app.exit(exception=KeyboardInterrupt)

    def current_mode() -> str | None:
        return get_active_mode() if get_active_mode else None

    def current_running_state() -> bool:
        return bool(get_is_running()) if get_is_running else False

    def current_queued_count() -> int:
        return max(0, int(get_queued_count())) if get_queued_count else 0

    def get_prompt():
        return format_prompt_text(current_mode())

    def get_bottom_toolbar():
        return format_bottom_toolbar_html(
            bundle_name=bundle_name,
            session_id=session_id,
            active_mode=current_mode(),
            is_running=current_running_state(),
            queued_count=current_queued_count(),
        )

    return PromptSession(
        message=get_prompt,
        bottom_toolbar=get_bottom_toolbar,
        completer=SlashCommandCompleter(
            commands,
            mode_shortcuts=mode_shortcuts,
            skill_shortcuts=skill_shortcuts,
            mcp_prompts=mcp_prompts,
            mode_names=mode_names,
            skill_names=skill_names,
            model_names=model_names,
        ),
        complete_while_typing=True,
        auto_suggest=AutoSuggestFromHistory(),
        history=history,
        key_bindings=key_bindings,
        multiline=True,
        prompt_continuation="",
        enable_history_search=True,
        reserve_space_for_menu=6,
        style=Style.from_dict(
            {
                "bottom-toolbar": f"noreverse bg:{TOKENS['bg_chrome']} fg:{TOKENS['dim']}",
            }
        ),
    )
