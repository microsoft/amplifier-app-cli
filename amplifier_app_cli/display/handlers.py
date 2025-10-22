"""Event display handlers for CLI output."""

import re

from rich.console import Console
from rich.table import Table

from ..events.schemas import AssistantMessage
from ..events.schemas import MessageEvent
from ..events.schemas import ToolCall
from ..events.schemas import ToolResult
from ..events.schemas import UserMessage
from ..profile_system.schema import UIConfig
from .formatters import format_tool_arguments
from .formatters import format_tree_line
from .formatters import truncate_output

console = Console()


def markdown_to_rich_markup(content: str) -> str:
    """Convert markdown to Rich markup format (avoids centering issues).

    Converts:
    - **bold** → [bold]bold[/bold]
    - *italic* → [italic]italic[/italic]
    - `code` → [code]code[/code]
    - ## headers → Bold text (no centering)
    - Lists and other elements → Plain text

    Args:
        content: Markdown content

    Returns:
        Content with Rich markup
    """
    # Convert markdown to Rich markup
    result = content

    # Remove headers but keep text as bold
    result = re.sub(r"^#{1,6}\s+(.+)$", r"[bold]\1[/bold]", result, flags=re.MULTILINE)

    # Convert markdown bold to Rich bold
    result = re.sub(r"\*\*(.+?)\*\*", r"[bold]\1[/bold]", result)

    # Convert markdown italic to Rich italic
    result = re.sub(r"\*(.+?)\*", r"[italic]\1[/italic]", result)
    result = re.sub(r"_(.+?)_", r"[italic]\1[/italic]", result)

    # Convert markdown code to Rich code
    result = re.sub(r"`([^`]+)`", r"[code]\1[/code]", result)

    # Remove horizontal rules
    result = re.sub(r"^[\s-]{3,}$", "", result, flags=re.MULTILINE)

    return result


def handle_event(event: MessageEvent, config: UIConfig) -> None:
    """Main event dispatcher.

    Routes events to specific display handlers based on type.

    Args:
        event: Message event to display
        config: UI configuration settings
    """
    if isinstance(event, UserMessage):
        display_user_message(event, config)
    elif isinstance(event, AssistantMessage):
        display_assistant_message(event, config)
    elif isinstance(event, ToolCall):
        display_tool_call(event, config)
    elif isinstance(event, ToolResult):
        display_tool_result(event, config)


def display_user_message(event: UserMessage, config: UIConfig) -> None:
    """Display user message event.

    Args:
        event: User message event
        config: UI configuration (unused but kept for consistency)
    """
    console.print()


def display_assistant_message(event: AssistantMessage, config: UIConfig) -> None:
    """Display assistant message with optional markdown rendering.

    Args:
        event: Assistant message event
        config: UI configuration
    """
    if not event.content:
        return

    # Clear any lingering status line before rendering
    console.print()

    table = Table(show_header=False, show_edge=False, box=None, padding=0)
    table.add_column(width=2, no_wrap=True)
    table.add_column()

    if config.render_markdown:
        # Convert markdown to Rich markup to avoid centering
        rich_markup = markdown_to_rich_markup(event.content.strip())
        table.add_row("●", rich_markup)
    else:
        table.add_row("●", event.content.strip())

    console.print(table)
    console.print()


def display_tool_call(event: ToolCall, config: UIConfig) -> None:
    """Display tool call with formatted arguments.

    Args:
        event: Tool call event
        config: UI configuration
    """
    args_display = format_tool_arguments(event.arguments, config.max_arg_length)
    console.print(f"[green]●[/green] {event.name}{args_display}")


def display_tool_result(event: ToolResult, config: UIConfig) -> None:
    """Display tool result with optional truncation.

    Args:
        event: Tool result event
        config: UI configuration
    """
    if not event.output:
        console.print()
        return

    lines, total_lines = truncate_output(event.output, config.tool_output_lines)

    if config.use_tree_formatting:
        for i, line in enumerate(lines):
            formatted = format_tree_line(line.strip(), i == 0)
            console.print(formatted)
    else:
        for line in lines:
            console.print(f"  {line.strip()}")

    if config.tool_output_lines != -1 and total_lines > len(lines):
        more_lines = total_lines - len(lines)
        console.print(f"     ... ({more_lines} more lines)")

    console.print()
