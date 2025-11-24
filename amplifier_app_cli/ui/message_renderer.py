"""Single source of truth for message rendering.

This module provides the canonical rendering functions for user and assistant
messages, used consistently across live chat, history display, and replay mode.

Zero duplication: All message rendering goes through these functions.
"""

from rich.console import Console

from ..console import Markdown


def render_message(message: dict, console: Console, *, show_thinking: bool = False) -> None:
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


def _render_assistant_message(message: dict, console: Console, show_thinking: bool) -> None:
    """Render assistant message with green prefix and markdown."""
    text_blocks, thinking_blocks = _extract_content_blocks(message, show_thinking=show_thinking)

    # Skip rendering if message is empty (tool-only messages)
    if not text_blocks and not thinking_blocks:
        return

    console.print("\n[bold green]Amplifier:[/bold green]")

    # Render text blocks with default styling
    if text_blocks:
        console.print(Markdown("\n".join(text_blocks)))

    # Render thinking blocks with dim styling
    for thinking in thinking_blocks:
        console.print(Markdown(f"\n💭 **Thinking:**\n{thinking}", style="dim"))


def _extract_content_blocks(message: dict, *, show_thinking: bool = False) -> tuple[list[str], list[str]]:
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
                text_parts.append(f"\n[dim]💭 Thinking: {thinking}[/dim]\n")
        return "\n".join(text_parts)

    # Fallback for unexpected formats
    return str(content)
