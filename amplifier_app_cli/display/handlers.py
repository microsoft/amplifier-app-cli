"""Event display handlers for CLI output."""

from rich.console import Console
from rich.console import ConsoleOptions
from rich.console import RenderResult
from rich.markdown import Heading
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text

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


class LeftAlignedHeading(Heading):
    """Custom Heading class that renders left-aligned instead of centered."""

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        """Render heading left-aligned instead of centered.

        Args:
            console: Rich console
            options: Console options

        Yields:
            Renderable elements
        """
        text = self.text
        text.justify = "left"  # Override the default center justification

        if self.tag == "h1":
            # h1: Bold text without panel
            yield Text("")
            yield text
            yield Text("")
        else:
            # h2+: Just the styled text
            yield text


class LeftAlignedMarkdown(Markdown):
    """Custom Markdown renderer with left-aligned headings."""

    # Override the elements class variable with our custom heading
    # Key must be "heading_open" to match markdown-it token type
    elements = {**Markdown.elements, "heading_open": LeftAlignedHeading}


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
        # Use custom left-aligned markdown renderer
        table.add_row("●", LeftAlignedMarkdown(event.content.strip(), justify="left"))
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
