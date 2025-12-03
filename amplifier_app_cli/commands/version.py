"""Version command for Amplifier CLI."""

import click
from rich.console import Console
from rich.table import Table

from ..utils.version import get_version, get_version_info

console = Console()


@click.command()
@click.option("--verbose", "-v", is_flag=True, help="Show detailed version information")
def version(verbose: bool):
    """Show Amplifier version information.

    Displays the current version in format: YYYY.MM.DD-<sha>
    Example: 2025.12.02-abc1234

    Use --verbose for detailed breakdown including install type.
    """
    info = get_version_info()

    if not verbose:
        # Simple output
        console.print(f"amplifier {info.display}")
        return

    # Verbose output with details
    console.print()
    console.print("[bold cyan]Amplifier Version[/bold cyan]")
    console.print()

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="dim")
    table.add_column("Value")

    table.add_row("Version", f"[bold]{info.display}[/bold]")

    if info.sha:
        table.add_row("Commit SHA", info.sha)

    if info.date:
        table.add_row("Commit Date", info.date)

    # Install type
    if info.is_local:
        install_type = "Local (editable)"
        if info.has_changes:
            install_type += " [yellow]with uncommitted changes[/yellow]"
    else:
        install_type = "Package (git)"

    table.add_row("Install Type", install_type)

    console.print(table)
    console.print()
