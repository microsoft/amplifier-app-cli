"""Shared utilities for command messaging.

This module provides utilities for consistent messaging across CLI commands.
"""

from rich.panel import Panel

from amplifier_app_cli.console import console


def show_info_panel(
    title: str,
    message: str,
    style: str = "cyan",
) -> None:
    """Show an information panel.

    Args:
        title: Panel title
        message: Panel content
        style: Border style color
    """
    console.print()
    console.print(
        Panel(
            message, border_style=style, title=title, title_align="left"
        )
    )
