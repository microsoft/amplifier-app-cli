"""Registry command - list modules from amplifier-modules registry."""

from __future__ import annotations

import json

import click
from rich.table import Table

from ...console import console
from ..client import RegistryClient


@click.command("registry")
@click.option(
    "--type",
    "-t",
    type=click.Choice(["agent", "behavior", "provider", "bundle", "context"]),
    help="Filter by module type",
)
@click.option(
    "--verified",
    is_flag=True,
    help="Show only verified modules",
)
@click.option(
    "--json",
    "output_json",
    is_flag=True,
    help="Output as JSON",
)
def registry_command(type: str | None, verified: bool, output_json: bool):
    """List available modules from the amplifier-modules registry.

    This command shows modules available in the central registry.
    To see installed modules, use 'amplifier module list'.
    """
    client = RegistryClient()

    try:
        # Build filters
        filters = {}
        if type:
            filters["type"] = type
        if verified:
            filters["verified"] = True

        # Fetch modules
        modules = client.list_modules(filters=filters if filters else None)

        # JSON output
        if output_json:
            output = {"total": len(modules), "modules": modules}
            print(json.dumps(output, indent=2))
            return

        # Human-readable output
        if not modules:
            console.print("[yellow]No modules found matching criteria[/yellow]")
            console.print("\nTry without filters: [cyan]amplifier module registry[/cyan]")
            return

        # Build filter description
        filter_desc = []
        if type:
            filter_desc.append(f"type={type}")
        if verified:
            filter_desc.append("verified only")
        filter_str = f" ({', '.join(filter_desc)})" if filter_desc else ""

        table = Table(
            title=f"Available Modules{filter_str} ({len(modules)})",
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("Name", style="green", no_wrap=True)
        table.add_column("Version", style="cyan")
        table.add_column("Type", style="yellow")
        table.add_column("Description")
        table.add_column("Author", style="dim")

        for module in modules:
            # Format name with verification badge
            name = module["name"]
            if module.get("verified"):
                name = f"✓ {name}"

            # Truncate long descriptions
            desc = module.get("description", "")
            if len(desc) > 60:
                desc = desc[:57] + "..."

            # Format author
            author = module.get("author", "")
            author_github = module.get("author_github", "")
            if author_github:
                author = f"{author} ({author_github})"

            table.add_row(
                name,
                module.get("version", ""),
                module.get("type", ""),
                desc,
                author,
            )

        console.print(table)
        console.print()
        console.print("[dim]Legend: ✓ = Verified by Amplifier team[/dim]")
        console.print()
        console.print("[dim]To install a module:[/dim]")
        console.print("  [cyan]amplifier module add <name>[/cyan]")
        console.print()
        console.print("[dim]For more details:[/dim]")
        console.print("  [cyan]amplifier module info <name>[/cyan]")

    except Exception as e:
        console.print(f"[red]Error:[/red] Failed to fetch registry: {e}")
        console.print("\n[dim]Possible solutions:[/dim]")
        console.print("  1. Check your internet connection")
        console.print("  2. Try again in a few moments")


def registry_command_func(type: str | None, verified: bool, output_json: bool):
    """Wrapper function for calling from code."""
    registry_command.callback(type=type, verified=verified, output_json=output_json)
