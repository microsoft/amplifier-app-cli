"""Event display handlers for CLI output."""

from typing import Any

from amplifier_core.models import HookResult
from rich.console import Console
from rich.console import ConsoleOptions
from rich.console import RenderResult
from rich.markdown import Heading
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text

from ..profile_system.schema import UIConfig
from .formatters import format_tool_arguments
from .formatters import format_tree_line
from .formatters import truncate_output

console = Console()


class LeftAlignedHeading(Heading):
    """Custom Heading class that renders left-aligned with proper spacing."""

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        """Render heading left-aligned instead of centered.

        Args:
            console: Rich console
            options: Console options

        Yields:
            Renderable elements
        """
        text = self.text
        text.justify = "left"  # Override default center
        text.stylize("bold")  # Make headings bold

        # h2 gets spacing before (like Rich does)
        if self.tag == "h2":
            yield Text()

        # All headings: just yield the styled text (Rich handles spacing via new_line)
        yield text


class LeftAlignedMarkdown(Markdown):
    """Custom Markdown renderer with left-aligned headings.

    Uses custom Heading that renders left-aligned without panels.
    Preserves Rich's newline handling for proper spacing.
    """

    elements = {**Markdown.elements, "heading_open": LeftAlignedHeading}


def create_display_hooks(config: UIConfig) -> dict[str, Any]:
    """Create display hook handlers configured with UI settings.

    Returns handlers that can be registered on core HookRegistry.

    Args:
        config: UI configuration settings

    Returns:
        Dictionary mapping event names to handler functions
    """

    async def handle_prompt_submit(event: str, data: dict) -> HookResult:
        """Handle user prompt submission."""
        console.print()  # Blank line before assistant response
        return HookResult(action="continue")

    async def handle_assistant_response(event: str, data: dict) -> HookResult:
        """Handle assistant response - display with markdown rendering.

        This is a custom display event emitted by main.py after session.execute().
        """
        # Extract content from data
        event_data = data.get("data", {})
        content = event_data.get("content", "")

        if not content:
            return HookResult(action="continue")

        # Clear any lingering status line
        console.print()

        if config.render_markdown:
            # Render markdown using TinkerTasker pattern with LeftAlignedMarkdown
            table = Table(show_header=False, show_edge=False, box=None, padding=0)
            table.add_column(width=2, no_wrap=True)  # For the dot
            table.add_column()  # For the content
            table.add_row("●", LeftAlignedMarkdown(content.strip()))
            console.print(table)
            console.print()
        else:
            # Plain text in table
            table = Table(show_header=False, show_edge=False, box=None, padding=0)
            table.add_column(width=2, no_wrap=True)
            table.add_column()
            table.add_row("●", content.strip())
            console.print(table)
            console.print()

        return HookResult(action="continue")

    async def handle_tool_pre(event: str, data: dict) -> HookResult:
        """Handle tool pre-execution - display tool call."""
        event_data = data.get("data", {})
        tool_name = event_data.get("tool", "unknown")
        args = event_data.get("args", {})

        args_display = format_tool_arguments(args, config.max_arg_length)
        console.print(f"[green]●[/green] {tool_name}{args_display}")
        return HookResult(action="continue")

    async def handle_tool_post(event: str, data: dict) -> HookResult:
        """Handle tool post-execution - display tool result."""
        event_data = data.get("data", {})
        result = event_data.get("result", {})

        if not result:
            console.print()
            return HookResult(action="continue")

        # Extract output from result
        if isinstance(result, dict):
            output = result.get("output", str(result))
        else:
            output = str(result)

        lines, total_lines = truncate_output(str(output), config.tool_output_lines)

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
        return HookResult(action="continue")

    return {
        "prompt:submit": handle_prompt_submit,
        "display:assistant_response": handle_assistant_response,
        "tool:pre": handle_tool_pre,
        "tool:post": handle_tool_post,
    }
