"""Shared deprecation utilities for deprecated commands.

This module provides a single source of truth for deprecation messaging,
ensuring consistency across help output and runtime warnings.
"""

from rich.panel import Panel

from amplifier_app_cli.console import console

# Migration guide URL - single source of truth
MIGRATION_GUIDE_URL = "https://github.com/microsoft/amplifier/blob/main/docs/MIGRATION_COLLECTIONS_TO_BUNDLES.md"

# Section header for deprecated commands list (simple, like "Commands")
DEPRECATED_SECTION_HEADER = "Deprecated Commands"


def show_deprecation_warning(
    deprecated_name: str,
    alternative_commands: list[tuple[str, str]],
) -> None:
    """Show deprecation warning for a deprecated command group.

    Args:
        deprecated_name: Name of the deprecated feature (e.g., "Collections", "Profiles")
        alternative_commands: List of (command, description) tuples for alternatives
    """
    # Build the alternative commands list
    commands_text = "\n".join(f"  • [dim]{cmd}[/dim] - {desc}" for cmd, desc in alternative_commands)

    warning_text = (
        f"[yellow bold]⚠ {deprecated_name} are deprecated.[/yellow bold]\n\n"
        f"Use [cyan]amplifier bundle[/cyan] commands instead:\n"
        f"{commands_text}\n"
        f"  • [dim]See: amplifier bundle --help[/dim]\n\n"
        f"[dim]Migration guide for developers:[/dim]\n"
        f"[link={MIGRATION_GUIDE_URL}]{MIGRATION_GUIDE_URL}[/link]"
    )

    console.print()
    console.print(Panel(warning_text, border_style="yellow", title="Deprecated", title_align="left"))


# Pre-configured deprecation warnings for specific command groups


def show_collection_deprecation_warning() -> None:
    """Show deprecation warning for collection commands."""
    show_deprecation_warning(
        deprecated_name="Collections",
        alternative_commands=[
            ("amplifier bundle use <name>", "Set active bundle"),
            ("amplifier bundle add <git-url>", "Register a bundle"),
            ("amplifier bundle list", "List available bundles"),
        ],
    )


def show_profile_deprecation_warning() -> None:
    """Show deprecation warning for profile commands."""
    show_deprecation_warning(
        deprecated_name="Profiles",
        alternative_commands=[
            ("amplifier bundle current", "Show active configuration"),
            ("amplifier bundle use <name>", "Set active bundle"),
            ("amplifier bundle clear", "Reset to default (foundation)"),
        ],
    )
