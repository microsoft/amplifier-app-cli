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
            source: Message source for context (e.g., "hook:python-check")

        Messages are formatted as tool output rather than system errors.
        The source is shown as a label prefix, making it clear this is
        feedback from a tool rather than an Amplifier error.

        Messages are indented based on current nesting depth to align
        with sub-session output formatting.
        """
        # Get indentation prefix for current nesting level
        indent = self._get_indent()

        # Extract tool name from source (e.g., "hook:python-check" -> "python-check")
        tool_name = source.split(":", 1)[-1] if ":" in source else source

        # Map level to color for the tool label
        level_colors = {
            "info": "cyan",
            "warning": "yellow",
            "error": "red",
        }
        color = level_colors.get(level, "cyan")

        # Format as tool output: [tool-name] message
        # This looks like tool feedback, not a system error
        self.console.print(f"{indent}[{color}][{tool_name}][/{color}] {message}")

        # Log at debug level (user already sees the message via console.print)
        logger.debug(
            f"Hook message displayed: {message}",
            extra={
                "source": source,
                "level": level,
                "nesting_depth": self._nesting_depth,
            },
        )
