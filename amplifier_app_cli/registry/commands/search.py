"""Search command - search for modules in the registry by keyword."""

from __future__ import annotations

import json

import click
from rich.panel import Panel

from ...console import console
from ..client import RegistryClient


def _render_relevance_bar(relevance: int, width: int = 10) -> str:
    """Render a relevance score as a progress bar.

    Args:
        relevance: Relevance score (0-100)
        width: Bar width in characters

    Returns:
        Colored bar string
    """
    filled = int((relevance / 100) * width)
    empty = width - filled
    bar = "█" * filled + "░" * empty
    return f"[cyan]{bar}[/cyan] {relevance}%"


@click.command("search")
@click.argument("query")
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
def search_command(query: str, type: str | None, verified: bool, output_json: bool):
    """Search for modules in the registry by keyword.

    Searches module names, descriptions, and tags for matches.
    Results are ranked by relevance.

    Example:

        amplifier module search "code review"
    """
    client = RegistryClient()

    try:
        # Build filters
        filters = {}
        if type:
            filters["type"] = type
        if verified:
            filters["verified"] = True

        # Search
        results = client.search(query, filters=filters if filters else None)

        # JSON output
        if output_json:
            output = {"query": query, "total": len(results), "results": results}
            print(json.dumps(output, indent=2))
            return

        # Human-readable output
        if not results:
            console.print(f'[yellow]No modules found matching "{query}"[/yellow]')
            console.print("\n[dim]Try:[/dim]")
            console.print("  - Using different keywords")
            console.print("  - Removing filters (--type, --verified)")
            console.print("  - Browsing all modules: [cyan]amplifier module registry[/cyan]")
            return

        console.print(f'\n[bold]Found {len(results)} modules matching "{query}":[/bold]\n')

        for module in results:
            # Build header with verification badge
            name = module["name"]
            version = module.get("version", "")
            if module.get("verified"):
                name = f"✓ {name}"
            header = f"{name} ({version})"

            # Build content
            desc = module.get("description", "No description")
            relevance_bar = _render_relevance_bar(module["relevance"])

            # Tags
            tags = module.get("tags", [])
            tags_str = f"Tags: {', '.join(tags)}" if tags else ""

            content_parts = [desc]
            if tags_str:
                content_parts.append(f"\n[dim]{tags_str}[/dim]")
            content_parts.append(f"\nRelevance: {relevance_bar}")

            content = "".join(content_parts)

            # Render panel
            console.print(Panel(content, title=header, border_style="cyan", padding=(0, 1)))
            console.print()

        console.print("[dim]To view details:[/dim]")
        console.print("  [cyan]amplifier module info <name>[/cyan]")
        console.print()
        console.print("[dim]To install:[/dim]")
        console.print("  [cyan]amplifier module add <name>[/cyan]")

    except Exception as e:
        console.print(f"[red]Error:[/red] Failed to search registry: {e}")
        console.print("\n[dim]Possible solutions:[/dim]")
        console.print("  1. Check your internet connection")
        console.print("  2. Try again in a few moments")


def search_command_func(query: str, type: str | None, verified: bool, output_json: bool):
    """Wrapper function for calling from code."""
    search_command.callback(query=query, type=type, verified=verified, output_json=output_json)
