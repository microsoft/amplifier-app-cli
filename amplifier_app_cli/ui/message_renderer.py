"""Single source of truth for message rendering.

This module provides the canonical rendering functions for user and assistant
messages, used consistently across live chat, history display, and replay mode.

Zero duplication: All message rendering goes through these functions.
"""

from collections.abc import Mapping

from rich.console import Console

from ..console import Markdown


def is_displayable_session_message(message: Mapping[str, object]) -> bool:
    """Return whether a persisted message belongs in session history or replay.

    Persisted ephemeral reminder envelopes remain part of the session context,
    but do not represent user-facing conversation history.
    """
    if not isinstance(message, Mapping):
        return False

    role = message.get("role")
    if role == "assistant":
        return True
    if role != "user":
        return False

    metadata = message.get("metadata")
    is_persisted_ephemeral_reminder = (
        isinstance(metadata, Mapping)
        and metadata.get("ephemeral") is True
        and metadata.get("persisted") is True
        and _is_reminder_content(message.get("content"))
    )
    return not is_persisted_ephemeral_reminder


def _is_reminder_content(content: object) -> bool:
    """Recognize only complete persisted reminder envelopes."""
    if isinstance(content, str):
        return _is_reminder_envelope(content.strip())

    if not isinstance(content, list) or not content:
        return False

    found_envelope = False
    for block in content:
        if not isinstance(block, Mapping) or block.get("type") != "text":
            return False
        text = block.get("text")
        if not isinstance(text, str):
            return False
        stripped_text = text.strip()
        if stripped_text:
            if not _is_reminder_envelope(stripped_text):
                return False
            found_envelope = True
    return found_envelope


def _is_reminder_envelope(text: str) -> bool:
    """Recognize one complete singular or plural reminder wrapper."""
    singular_open = "<system-reminder"
    singular_close = "</system-reminder>"
    plural_open = "<system-reminders>"
    plural_close = "</system-reminders>"

    if text.startswith(plural_open) and text.endswith(plural_close):
        body = text[len(plural_open) : -len(plural_close)]
        return not _contains_tag(body, "system-reminders")

    if not text.startswith(singular_open) or not text.endswith(singular_close):
        return False

    opening_end = len(singular_open)
    if text[opening_end : opening_end + 1] == ">":
        opening_end += 1
    elif text.startswith(' source="', opening_end):
        source_end = text.find('"', opening_end + len(' source="'))
        if source_end == -1 or text[source_end + 1 : source_end + 2] != ">":
            return False
        opening_end = source_end + 2
    else:
        return False

    body = text[opening_end : -len(singular_close)]
    return not _contains_tag(body, "system-reminder")


def _contains_tag(text: str, name: str) -> bool:
    """Return whether text contains an opening or closing tag with this exact name."""
    for prefix in (f"<{name}", f"</{name}"):
        position = text.find(prefix)
        while position != -1:
            boundary = position + len(prefix)
            if text[boundary : boundary + 1] in (">", " ", "\t", "\r", "\n"):
                return True
            position = text.find(prefix, boundary)
    return False


def render_message(
    message: dict,
    console: Console,
    *,
    show_thinking: bool = False,
    show_label: bool = True,
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
        show_label: Whether to print the 'Amplifier:' label prefix (default: True).
            Pass False when the streaming overlay has already printed the label so
            it appears exactly once.
    """
    role = message.get("role")

    if role == "user":
        _render_user_message(message, console)
    elif role == "assistant":
        _render_assistant_message(message, console, show_thinking, show_label)
    # Skip system/developer (implementation details, not conversation)


def _render_user_message(message: dict, console: Console) -> None:
    """Render user message with green prefix (matches live prompt style)."""
    content = _extract_content(message, show_thinking=False)
    console.print(f"\n[bold green]>[/bold green] {content}")


def _render_assistant_message(
    message: dict, console: Console, show_thinking: bool, show_label: bool = True
) -> None:
    """Render assistant message with green prefix and markdown."""
    text_blocks, thinking_blocks = _extract_content_blocks(
        message, show_thinking=show_thinking
    )

    # Skip rendering if message is empty (tool-only messages)
    if not text_blocks and not thinking_blocks:
        return

    if show_label:
        console.print("\n[bold green]Amplifier:[/bold green]")

    # Render text blocks with default styling
    if text_blocks:
        console.print(Markdown("\n".join(text_blocks)))

    # Render thinking blocks with dim styling
    for thinking in thinking_blocks:
        console.print(Markdown(f"\n💭 **Thinking:**\n{thinking}", style="dim"))


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
                text_parts.append(f"\n[dim]💭 Thinking: {thinking}[/dim]\n")
        return "\n".join(text_parts)

    # Fallback for unexpected formats
    return str(content)
