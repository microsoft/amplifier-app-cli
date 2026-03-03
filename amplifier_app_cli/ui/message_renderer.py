"""Single source of truth for message rendering.

This module provides the canonical rendering functions for user and assistant
messages, used consistently across live chat, history display, and replay mode.

Zero duplication: All message rendering goes through these functions.
"""

import re

from markdown_it import MarkdownIt
from rich.console import Console

from ..console import Markdown

_SHELL_LEXERS = frozenset({"bash", "sh", "shell", "zsh", "fish", "console"})

# Shared markdown-it parser for extracting code fence tokens.
# This correctly handles backticks inside fenced blocks (unlike regex).
_md_parser = MarkdownIt()

# Fixed-width rule (72 chars) for command block framing.
_RULE_WIDTH = 72
_RULE = "\u2500" * _RULE_WIDTH  # ────────...
_SCISSORS = "\u2702"  # ✂


def _print_framed_command(code: str, console: Console) -> None:
    """Print a shell command with visual framing for copy-paste.

    Renders:
        ───── ✂ Copy & Run ──────────────────────────────────────
        <blank line>
        <command as single logical line, soft_wrap=True>
        <blank line>
        ─────────────────────────────────────────────────────────

    The command is plain text with soft_wrap so the terminal handles visual
    wrapping. Triple-click copies the full command as one logical line.
    """
    # Top rule with scissors label
    label = f" {_SCISSORS} Copy & Run "
    top_rule = "\u2500" * 5 + label + "\u2500" * max(1, _RULE_WIDTH - 5 - len(label))
    console.print(f"\n[dim]{top_rule}[/dim]")

    # Command — plain text, single logical line, no Rich highlighting
    console.print()
    console.print(code, soft_wrap=True, highlight=False)
    console.print()

    # Bottom rule
    console.print(f"[dim]{_RULE}[/dim]")


def _render_content_with_copyable_commands(content: str, console: Console) -> None:
    """Render markdown, framing shell code blocks for copy-paste.

    Uses markdown-it-py to parse the AST — this correctly identifies code fence
    boundaries even when the command content contains backticks (e.g. commands
    that generate markdown with echo '```'). Regex fence matching breaks on
    these cases.

    Shell code blocks get visual framing (scissors rule + plain text + bottom
    rule). Non-shell code blocks render normally through Rich Markdown.
    """
    tokens = _md_parser.parse(content)
    lines = content.split("\n")

    # Collect shell fence tokens with their line ranges
    shell_fences: list[tuple[int, int, str]] = []
    for token in tokens:
        if token.type == "fence" and token.map:
            lang = ((token.info or "").split() or [""])[0]
            if lang in _SHELL_LEXERS:
                shell_fences.append((token.map[0], token.map[1], token.content))

    if not shell_fences:
        # No shell blocks — render everything through Markdown
        console.print(Markdown(content))
        return

    last_line = 0
    for start_line, end_line, code in shell_fences:
        # Render any markdown content before this shell block
        before = "\n".join(lines[last_line:start_line])
        if before.strip():
            console.print(Markdown(before))

        # Collapse backslash continuations and frame the command
        code = re.sub(r" *\\\n[ \t]*", " ", code).rstrip()
        _print_framed_command(code, console)

        last_line = end_line

    # Render any remaining content after the last shell block
    remaining = "\n".join(lines[last_line:])
    if remaining.strip():
        console.print(Markdown(remaining))


def render_message(
    message: dict, console: Console, *, show_thinking: bool = False
) -> None:
    """Render a single message (user or assistant).

    Single source of truth for message formatting. Used by:
    - Live chat (main.py)
    - History display (commands/session.py)
    - Replay mode (commands/session.py)

    Args:
        message: Message dictionary with 'role' and 'content'
        console: Rich Console instance for output
        show_thinking: Whether to include thinking blocks (default: False)
    """
    role = message.get("role")

    if role == "user":
        _render_user_message(message, console)
    elif role == "assistant":
        _render_assistant_message(message, console, show_thinking)
    # Skip system/developer (implementation details, not conversation)


def _render_user_message(message: dict, console: Console) -> None:
    """Render user message with green prefix (matches live prompt style)."""
    content = _extract_content(message, show_thinking=False)
    console.print(f"\n[bold green]>[/bold green] {content}")


def _render_assistant_message(
    message: dict, console: Console, show_thinking: bool
) -> None:
    """Render assistant message with green prefix and markdown."""
    text_blocks, thinking_blocks = _extract_content_blocks(
        message, show_thinking=show_thinking
    )

    # Skip rendering if message is empty (tool-only messages)
    if not text_blocks and not thinking_blocks:
        return

    console.print("\n[bold green]Amplifier:[/bold green]")

    # Render text blocks — shell code blocks get framing for copy-paste.
    # _render_content_with_copyable_commands parses the AST once and
    # short-circuits to plain Markdown when no shell fences are found.
    if text_blocks:
        _render_content_with_copyable_commands("\n".join(text_blocks), console)

    # Render thinking blocks with dim styling
    for thinking in thinking_blocks:
        console.print(Markdown(f"\n\U0001f4ad **Thinking:**\n{thinking}", style="dim"))


def _extract_content_blocks(
    message: dict, *, show_thinking: bool = False
) -> tuple[list[str], list[str]]:
    """Extract text and thinking blocks separately from message content.

    Handles multiple content formats:
    - String content (simple case)
    - Structured content (ContentBlocks from API)

    Args:
        message: Message dictionary
        show_thinking: Include thinking blocks in output

    Returns:
        Tuple of (text_blocks, thinking_blocks)
    """
    content = message.get("content", "")
    text_blocks = []
    thinking_blocks = []

    # String content (simple case)
    if isinstance(content, str):
        text_blocks.append(content)
        return text_blocks, thinking_blocks

    # Structured content (ContentBlocks)
    if isinstance(content, list):
        for block in content:
            if block.get("type") == "text":
                text_blocks.append(block.get("text", ""))
            elif block.get("type") == "thinking" and show_thinking:
                thinking_blocks.append(block.get("thinking", ""))
        return text_blocks, thinking_blocks

    # Fallback for unexpected formats
    return [str(content)], []


def _extract_content(message: dict, *, show_thinking: bool = False) -> str:
    """Extract displayable text from message content.

    Handles multiple content formats:
    - String content (simple case)
    - Structured content (ContentBlocks from API)
    - Thinking blocks (if show_thinking=True)

    Args:
        message: Message dictionary
        show_thinking: Include thinking blocks in output

    Returns:
        Displayable text content
    """
    content = message.get("content", "")

    # String content (simple case)
    if isinstance(content, str):
        return content

    # Structured content (ContentBlocks)
    if isinstance(content, list):
        text_parts = []
        for block in content:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "thinking" and show_thinking:
                thinking = block.get("thinking", "")
                text_parts.append(f"\n[dim]\U0001f4ad Thinking: {thinking}[/dim]\n")
        return "\n".join(text_parts)

    # Fallback for unexpected formats
    return str(content)
