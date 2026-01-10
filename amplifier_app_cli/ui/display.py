"""CLI display system implementation using rich terminal UX."""

import logging
from typing import Literal

from rich.console import Console

logger = logging.getLogger(__name__)

# Indentation for nested sessions (matches orchestrator output style)
NESTING_INDENT = "    "  # 4 spaces per nesting level


class CLIDisplaySystem:
    """Terminal-based display with Rich formatting.

    Supports nesting depth tracking to indent hook messages when running
    in sub-sessions (agent delegations). The nesting is managed via
    push_nesting()/pop_nesting() calls from the session spawner.
    """

    def __init__(self):
        self.console = Console()
        self._nesting_depth = 0

    def push_nesting(self) -> None:
        """Increase nesting depth (called when entering a sub-session)."""
        self._nesting_depth += 1
        logger.debug(f"Display nesting depth increased to {self._nesting_depth}")

    def pop_nesting(self) -> None:
        """Decrease nesting depth (called when exiting a sub-session)."""
        if self._nesting_depth > 0:
            self._nesting_depth -= 1
            logger.debug(f"Display nesting depth decreased to {self._nesting_depth}")

    @property
    def nesting_depth(self) -> int:
        """Current nesting depth (0 = root session)."""
        return self._nesting_depth

    def _get_indent(self) -> str:
        """Get indentation prefix for current nesting level."""
        return NESTING_INDENT * self._nesting_depth

    def show_message(
        self,
        message: str,
        level: Literal["info", "warning", "error"],
        source: str = "hook",
    ):
        """
        Display message with appropriate formatting and severity.

        Args:
            message: Message text to display
            level: Severity level (info/warning/error)
            source: Message source for context

        Messages are indented based on current nesting depth to align
        with sub-session output formatting.
        """
        # Map level to Rich style and icon
        styles = {
            "info": ("[green]\u2139\ufe0f[/green]", "green"),
            "warning": ("[yellow]\u26a0\ufe0f[/yellow]", "yellow"),
            "error": ("[red]\u274c[/red]", "red"),
        }

        icon, color = styles.get(level, ("[blue]\u2139\ufe0f[/blue]", "blue"))

        # Get indentation prefix for current nesting level
        nesting_indent = self._get_indent()

        # Handle multi-line messages by indenting subsequent lines
        lines = message.split("\n")
        first_line = lines[0]

        # Build prefix for first line
        prefix = f"{nesting_indent}{icon} [{color}]{level.upper()}[/{color}] "

        # Calculate indent for subsequent lines (nesting + icon ~2 + space + level + space)
        # Use spaces to align with content after the prefix
        content_indent = (
            nesting_indent + "         "
        )  # 9 spaces to align after "❌ ERROR "

        if len(lines) == 1:
            # Single line - simple case
            self.console.print(f"{prefix}{first_line} [dim]({source})[/dim]")
        else:
            # Multi-line - print first with source, indent rest
            self.console.print(f"{prefix}{first_line} [dim]({source})[/dim]")
            for line in lines[1:]:
                if line.strip():  # Skip empty lines
                    self.console.print(f"{content_indent}{line}")

        # Log at debug level (user already sees the message via console.print)
        logger.debug(
            f"Hook message displayed: {message}",
            extra={
                "source": source,
                "level": level,
                "nesting_depth": self._nesting_depth,
            },
        )
