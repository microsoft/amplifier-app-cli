"""Single source of truth for message rendering.

This module provides the canonical rendering functions for user and assistant
messages, used consistently across live chat, history display, and replay mode.

Zero duplication: All message rendering goes through these functions.
"""

from rich.console import Console
from .transcript_blocks import AnswerBlock
from .transcript_blocks import DebugBlock
from .transcript_blocks import UserBlock
from .ui_events import UiEventDispatcher


def render_message(
    message: dict,
    console: Console | None = None,
    *,
    show_thinking: bool = False,
    show_label: bool = True,
    dispatcher: UiEventDispatcher | None = None,
) -> None:
    """Render a single message (user or assistant).

    Single source of truth for message formatting. Used by:
    - Live chat (main.py)
    - History display (commands/session.py)
    - Replay mode (commands/session.py)

    Args:
        message: Message dictionary with 'role' and 'content'
        console: Rich Console instance when no dispatcher is supplied
        show_thinking: Whether to include thinking blocks (default: False)
        show_label: Whether to print the 'Amplifier:' label prefix (default: True).
            Pass False when the streaming overlay has already printed the label so
            it appears exactly once.
    """
    events = dispatcher
    if events is None:
        if console is None:
            raise TypeError("console or dispatcher is required")
        events = UiEventDispatcher(console)
    role = message.get("role")

    if role == "user":
        _render_user_message(message, events)
    elif role == "assistant":
        _render_assistant_message(message, events, show_thinking, show_label)
    # Skip system/developer (implementation details, not conversation)


def _render_user_message(message: dict, events: UiEventDispatcher) -> None:
    """Render a user message through the canonical transcript grammar."""
    content = _extract_content(message, show_thinking=False)
    metadata = message.get("metadata")
    mode = metadata.get("mode") if isinstance(metadata, dict) else None
    events.emit(UserBlock(content, mode=mode))


def _render_assistant_message(
    message: dict,
    events: UiEventDispatcher,
    show_thinking: bool,
    show_label: bool = True,
) -> None:
    """Render assistant message with green prefix and markdown."""
    content_blocks = _extract_content_blocks(message, show_thinking=show_thinking)

    # Skip rendering if message is empty (tool-only messages)
    if not content_blocks:
        return

    for index, (block_type, content) in enumerate(content_blocks):
        if index:
            events.gap()
        if block_type == "thinking":
            events.emit(
                DebugBlock(
                    tuple(content.splitlines() or [content]),
                    label="Thinking",
                    expanded=True,
                )
            )
        else:
            events.emit(
                AnswerBlock(
                    content,
                    label="Amplifier" if show_label and index == 0 else None,
                )
            )


def _extract_content_blocks(
    message: dict, *, show_thinking: bool = False
) -> list[tuple[str, str]]:
    """Extract displayable content blocks in their original order.

    Handles multiple content formats:
    - String content (simple case)
    - Structured content (ContentBlocks from API)

    Args:
        message: Message dictionary
        show_thinking: Include thinking blocks in output

    Returns:
        Ordered ``(block_type, content)`` pairs for rendering
    """
    content = message.get("content", "")

    # String content (simple case)
    if isinstance(content, str):
        return [("text", content)] if content else []

    # Structured content (ContentBlocks)
    if isinstance(content, list):
        content_blocks: list[tuple[str, str]] = []
        for block in content:
            if block.get("type") == "text":
                text = block.get("text", "")
                if text:
                    content_blocks.append(("text", text))
            elif block.get("type") == "thinking" and show_thinking:
                thinking = block.get("thinking", "")
                if thinking:
                    content_blocks.append(("thinking", thinking))
        return content_blocks

    # Fallback for unexpected formats
    return [("text", str(content))]


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
            elif block.get("type") == "image":
                text_parts.append("[Image attachment]")
            elif block.get("type") == "thinking" and show_thinking:
                thinking = block.get("thinking", "")
                text_parts.append(f"\n[dim]💭 Thinking: {thinking}[/dim]\n")
        return "\n".join(text_parts)

    # Fallback for unexpected formats
    return str(content)
