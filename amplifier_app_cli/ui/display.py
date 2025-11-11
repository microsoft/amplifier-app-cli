"""CLI display system implementation using rich terminal UX."""

import logging
from typing import Literal

from rich.console import Console

logger = logging.getLogger(__name__)


class CLIDisplaySystem:
    """Terminal-based display with Rich formatting."""

    def __init__(self):
        self.console = Console()

    def show_message(self, message: str, level: Literal["info", "warning", "error"], source: str = "hook"):
        """
        Display message with appropriate formatting and severity.

        Args:
            message: Message text to display
            level: Severity level (info/warning/error)
            source: Message source for context

        Logs the message and displays it to user with appropriate styling.
        """
        # Map level to Rich style and icon
        styles = {
            "info": ("[green]ℹ️[/green]", "green"),
            "warning": ("[yellow]⚠️[/yellow]", "yellow"),
            "error": ("[red]❌[/red]", "red"),
        }

        icon, color = styles.get(level, ("[blue]ℹ️[/blue]", "blue"))

        # Display to user
        self.console.print(f"{icon} [{color}]{level.upper()}[/{color}] {message} [dim]({source})[/dim]")

        # Also log
        log_methods = {"info": logger.info, "warning": logger.warning, "error": logger.error}

        log_fn = log_methods[level]
        log_fn(f"Hook user message: {message}", extra={"source": source, "level": level})
